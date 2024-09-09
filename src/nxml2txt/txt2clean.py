import argparse
import codecs
import glob
import sys
import os.path
import xml.etree.cElementTree as ET
import traceback
import gzip

__author__ = "Filip Ginter"


class Section:
    def __init__(self, title, charBeg, charEnd, titleCharBeg, titleCharEnd):
        self.title = title
        self.charBeg = charBeg
        self.charEnd = charEnd
        self.titleCharBeg = titleCharBeg
        self.titleCharEnd = titleCharEnd
        self.subsections = []
        self.parent = None
        self.spans = []

    def addsubsection(self, title, charBeg, charEnd, titleCharBeg, titleCharEnd):
        # Is this my own subsection?
        if charBeg >= self.charBeg and charEnd <= self.charEnd:
            # yup
            if self.subsections:
                assert charBeg >= self.subsections[-1].charEnd
            newS = Section(title, charBeg, charEnd, titleCharBeg, titleCharEnd)
            self.subsections.append(newS)
            newS.parent = self
            return newS
        # I guess this section should be following me
        assert charBeg >= self.charEnd
        return self.parent.addsubsection(
            title, charBeg, charEnd, titleCharBeg, titleCharEnd
        )

    def elem(self, actualSpanOffsets, inputTXT):
        E = ET.Element("section")
        if self.titleCharBeg is None and self.titleCharEnd is None:
            E.set("title", self.title)
        else:
            E.set("title", inputTXT[self.titleCharBeg : self.titleCharEnd])
        for spanID in self.spans:
            S = ET.SubElement(E, "span")
            cB, cE = actualSpanOffsets[spanID][0], actualSpanOffsets[spanID][1]
            if (cB, cE) == (None, None):
                S.set("charBeg", "None")
                S.set("charEnd", "None")
            else:
                S.set("charBeg", cB)
                S.set("charEnd", cE)
        #                S.set("charBeg",unicode(cB))
        #                S.set("charEnd",unicode(cE))

        for subS in self.subsections:
            E.append(subS.elem(actualSpanOffsets, inputTXT))
        return E


def is_zip_file(filename):
    return filename.endswith(".gz")


def read_lines(filename):
    if is_zip_file(filename):
        with gzip.open(filename, "rb") as f:
            for line in f:
                yield line
    else:
        with open(filename, "rt") as f:
            for line in f:
                yield line


def read_text(filename):
    return "".join(read_lines(filename))


def interesting_spans(soFileName):
    mainSection = None
    currentSection = None
    spans = []  # list of (element,beg,end,spanTxt)
    ranges = {}  # key: latest seen element of the given type, value: (beg,end)
    for line in read_lines(soFileName):
        line = line.strip()
        if not line:
            continue
        # line=unicode(line,"utf-8")
        line = line.split("\t")
        if len(line) == 1:
            continue
        elif len(line) == 2:
            spanId, spanOff = line[:2]
            spanTxt = ""
        elif len(line) >= 3:
            spanId, spanOff, spanTxt = line[:3]
        element, charBeg, charEnd = spanOff.split()
        charBeg, charEnd = int(charBeg), int(charEnd)
        ranges[element] = (charBeg, charEnd)
        if element == "body":
            currentSection = currentSection.addsubsection(
                element, charBeg, charEnd, None, None
            )
            continue
        if element == "article" and charBeg == 0:
            assert currentSection is None
            currentSection = Section(element, charBeg, charEnd, None, None)
            mainSection = currentSection
            continue
        if element == "sec":
            # Do this only in the body
            if "body" not in ranges:
                continue
            bodyB, bodyE = ranges["body"]
            if not (charBeg >= bodyB and charEnd <= bodyE):
                continue
            currentSection = currentSection.addsubsection(
                element, charBeg, charEnd, None, None
            )
            continue
        if element == "title":
            # Does this belong to a section?
            if "sec" not in ranges:
                continue  # nope
            if currentSection.title == "sec" and currentSection.titleCharBeg is None:
                currentSection.title = spanTxt.strip()
                currentSection.titleCharBeg = charBeg
                currentSection.titleCharEnd = charEnd
            # This doesn't really work with labels
            # secBeg,secEnd=ranges[u"sec"]
            # if secBeg!=charBeg:
            #     continue #nope
            # Any title past this point is a fresh section title
        if element == "article-id":
            if len(line) != 4:
                continue
            if line[3] == 'pub-id-type="pmc"':
                articleID = line[2]
        if element in ("article-title", "abstract"):
            # These are only allowed before the body
            if "body" in ranges:
                continue
            currentSection = currentSection.addsubsection(
                element, charBeg, charEnd, None, None
            )  # making dummy sections for these
        elif element in ("p", "title"):
            # This is only allowed inside body
            if "body" not in ranges:
                continue
            bodyB, bodyE = ranges["body"]
            if not (charBeg >= bodyB and charEnd <= bodyE):
                continue
        # The elements of interest here are permissible now
        if element in ("p", "title", "article-title", "abstract"):
            if spans:
                prev = spans[-1]
                if prev[1] <= charBeg and prev[2] >= charEnd:  #
                    continue  # Skip if the previous span crosses this one

            spans.append((element, charBeg, charEnd, spanTxt))
            currentSection.spans.append(len(spans) - 1)
        if element in ("inline-formula", "math"):
            if spans:
                prev = spans[-1]
                if prev[1] == charBeg and prev[2] == charEnd:  #
                    # math spanning whole paragraph
                    spans.pop(-1)
                    currentSection.spans.pop(-1)

    return articleID, mainSection, spans


# Magic string that doesn't appear in PubMed document text.
UUID_STRING = "7f4efe60-4f29-434a-aa61-609fd066dcaa"

# The standoff conversion abbreviates long control spans with this
ELLIPSIS = "[[[...]]]"


def validate_text(text, control):
    # unescape
    control = (
        control.strip()
        .replace("\\\\", UUID_STRING)
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace(UUID_STRING, "\\")
        .strip()
    )

    idx = control.find(ELLIPSIS)
    if idx == -1:
        assert text == control, (text, control)
    else:
        prefix = control[:idx]
        suffix = control[idx + len(ELLIPSIS) :]
        assert text.startswith(prefix), (text, prefix)
        assert text.endswith(suffix), (text, suffix)


def skip_element(element, options):
    # don't skip anything by default
    if options is None:
        return False
    elif element in ("abstract", "article-title") and options.no_abstract:
        return True
    else:
        return False


def clean_text(spans, txt, options):
    cleaned_offsets = []
    cleaned_texts = []
    offset = 0
    for element, start, end, control in spans:
        if skip_element(element, options):
            cleaned_offsets.append((None, None))
            continue

        while start <= end and start < len(txt) and txt[start].isspace():
            start += 1
        while end >= start and end <= len(txt) and txt[end - 1].isspace():
            end -= 1
        span_text = txt[start:end]
        validate_text(span_text, control)

        # use newlines to separate sections
        span_text = span_text + "\n\n"

        cleaned_texts.append(span_text)
        cleaned_offsets.append((offset, offset + len(span_text)))
        offset += len(span_text)
    return cleaned_offsets, "".join(cleaned_texts)


def rootname(filename):
    """Return basename root without extensions."""
    name = os.path.basename(filename)
    root, ext = os.path.splitext(name)
    while ext:
        root, ext = os.path.splitext(root)
    return root


def get_doc_pairs(textdir, sodir, options=None):
    if options and options.zipped:
        txtglob = "*.txt.gz"
        soglob = "*.so.gz"
    else:
        txtglob = "*.txt"
        soglob = "*.so"
    textfiles = glob.glob(os.path.join(textdir, txtglob))
    textfiles.sort()
    sofiles = glob.glob(os.path.join(sodir, soglob))
    sofiles.sort()
    assert len(textfiles) == len(sofiles), ".txt and .so counts differ in %s and %s" % (
        textdir,
        sodir,
    )
    pairs = []
    for textfile, sofile in zip(textfiles, sofiles):
        assert rootname(textfile) == rootname(sofile), (textfile, sofile)
        pairs.append((textfile, sofile))
    return pairs


def indent(elem, level=0):
    i = "\n" + level * "  "
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = i + "  "
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
        for elem in elem:
            indent(elem, level + 1)
        if not elem.tail or not elem.tail.strip():
            elem.tail = i
    else:
        if level and (not elem.tail or not elem.tail.strip()):
            elem.tail = i
    return elem


def output_filenames(textfile, sofile, textdir, secdir, docid, options=None):
    outtext = os.path.join(textdir, "pmc_%s.txt" % docid)
    if secdir is None:
        outsec = None
    else:
        outsec = os.path.join(secdir, "pmc_%s_sections.xml" % docid)
    return outtext, outsec


def process_pair(textfile, sofile, textdir, secdir, options=None):
    try:
        text = read_text(textfile)
        docid, mainSection, spans = interesting_spans(sofile)
        actual_offsets, cleaned = clean_text(spans, text, options)

        outtext, outsec = output_filenames(
            textfile, sofile, textdir, secdir, docid, options
        )
        with codecs.open(outtext, "w", "utf-8") as f:
            f.write(cleaned)
        if outsec is not None:
            element = indent(mainSection.elem(actual_offsets, text))
            ET.ElementTree(element).write(outsec, "utf-8")
    except Exception:
        traceback.print_exc()
        sys.stderr.write("ERROR, SKIPPING:", textfile)
        for fn in (outtext, outsec):
            try:
                os.system("rm -f '%s'" % fn)
            except Exception:
                pass


def process_dir(textdir, sodir, options):
    pairs = get_doc_pairs(textdir, sodir)

    textout = options.textout
    if not os.path.exists(textout):
        os.makedirs(textout)

    secout = options.secout
    if secout and not os.path.exists(secout):
        os.makedirs(secout)

    for txtfile, sofile in pairs:
        process_pair(txtfile, sofile, textout, secout, options)


def main(argv):
    parser = argparse.ArgumentParser(
        description="Get clean text and section data from .txt and .so files."
    )
    parser.add_argument(
        "-a",
        "--no-abstract",
        action="store_true",
        help="Do not ouput abstract or title",
    )
    parser.add_argument(
        "-t",
        "--textout",
        metavar="DIR",
        required=True,
        help="Directory to store clean .txt files to",
    )
    parser.add_argument(
        "-s",
        "--secout",
        metavar="DIR",
        default=None,
        help="Directory to store section data XML to",
    )
    parser.add_argument(
        "-z",
        "--zipped",
        default=False,
        action="store_true",
        help="Process zipped (.gz) files",
    )
    parser.add_argument("dirs", nargs="+", help="Directories to process.")
    args = parser.parse_args(argv[1:])

    for dir_name in sorted(args.dirs):
        dir_name = dir_name.strip()
        process_dir(dir_name, dir_name, args)

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
