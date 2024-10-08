#!/usr/bin/env python

# Replaces the content of <tex-math> elements with approximately
# equivalent text strings in PMC NXML files. Requires catdvi.

# This is a component in a pipeline to convert PMC NXML files into
# text and standoffs. The whole pipeline can be run as
#
#    python rewritetex.py FILE.xml -s | python rewriteu2a.py - -s | python respace.py - -s | python standoff.py - FILE.{txt,so}

from __future__ import with_statement

import sys
import os
import re

from lxml import etree as ET

# How many seconds to wait for a SQLite lock to go away.
SQLITE_TIMEOUT = 30.0

# XML tag to use for elements whose text content has been rewritten
# by this script.
REWRITTEN_TAG = "n2t-tex"

# XML attribute to use for storing the original text and tag of
# rewritten elements
ORIG_TAG_ATTRIBUTE = "orig-tag"
ORIG_TEXT_ATTRIBUTE = "orig-text"

# command for invoking tex (-interaction=nonstopmode makes latex try
# to proceed on error without waiting for input.)
TEX_COMMAND = "latex -interaction=nonstopmode"

# directory into which to instruct tex to place its output.
if os.environ.get("TMPDIR"):
    TEX_OUTPUTDIR = os.environ["TMPDIR"]
else:
    TEX_OUTPUTDIR = "/tmp"

# command for invokind catdvi (-e 0 specifies output encoding in UTF-8,
# and -s sets sequential mode, which turns off attempt to reproduce
# layout such as sub- and superscript positioning.)
CATDVI_COMMAND = "catdvi -e 0 -s"

# path to on-disk caches of tex document -> text mappings
PICKLE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data/tex2txt.cache")
SQLITE_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data/tex2txt.db")

INPUT_ENCODING = "UTF-8"
OUTPUT_ENCODING = "UTF-8"

# pre-compiled regular expressions

# key declarations in tex documents
texdecl_re = re.compile(
    r"(\\(?:documentclass|usepackage|setlength|pagestyle)(?:\[[^\[\]]*\])?(?:\{[^\{\}]*\})*)"
)
# document start or end
texdoc_re = re.compile(r"(\\(?:begin|end)(?:\[[^\[\]]*\])?\{document\})")
# includes for "standard" tex packages
texstdpack_re = re.compile(
    r"\\usepackage\{(?:amsbsy|amsfonts|amsmath|amssymb|mathrsfs|upgreek|wasysym)\}"
)
# consequtive space
space_re = re.compile(r"\s+")
# initial and terminal space.
docstartspace_re = re.compile(r"^\s*")
docendspace_re = re.compile(r"\s*$")

##########


def normalize_tex(s):
    """
    Given the string content of a tex document, returns a normalized
    version of its content, abstracting away "standard" declarations
    and includes.
    """

    # Note: this could be made more effective by including synonymous
    # commands in tex and more removing content-neutral formatting
    # more aggressively.

    # remove "standard" package includes
    s = texstdpack_re.sub("", s)

    # remove header boilerplate declarations (superset of texstdpack_re)
    s = texdecl_re.sub(r"", s)

    # replace any amount of consequtive space by a single plain space
    s = space_re.sub(" ", s)

    # eliminate doc-initial and -terminal space.
    s = docstartspace_re.sub("", s)
    s = docendspace_re.sub("", s)

    return s


def ordall(d):
    """
    Given a dict with string values, returns an equivalent dict where
    the strings have been transformed into arrays of integers. This is
    a data "wrapper" to avoid a weird issue with cPickle where
    undefined unicode chars were modified in the pickling/unpickling
    process.  (Try to pickle unichr(0x10FF0C) to see if this affects
    your setup)
    """
    from copy import deepcopy

    d = deepcopy(d)
    for k in d.keys():
        d[k] = [ord(c) for c in d[k]]
    return d


def unordall(d):
    """
    Given a dict with integer list values, returns an equivalent dict
    where the int lists have been transformed into unicode strings.
    This is a data "wrapper" to avoid a weird issue with cPickle where
    undefined unicode chars were modified in the pickling/unpickling
    process.  (Try to pickle unichr(0x10FF0C) to see if this affects
    your setup)
    """
    from copy import deepcopy

    d = deepcopy(d)
    for k in d.keys():
        d[k] = "".join([chr(c) for c in d[k]])
    return d


class Cache(object):
    def __init__(self, map_=None):
        if map_ is None:
            self._map = {}
        else:
            self._map = map_

    def get(self, key):
        return self._map.get(key)

    def set(self, key, value):
        self._map[key] = value


class PickleCache(Cache):
    def __init__(self, map=None):
        super(PickleCache, self).__init__(map)

    def save(self, filename=PICKLE_CACHE_PATH):
        from pickle import dump as pickle_dump

        try:
            with open(filename, "wb") as cache_file:
                pickle_dump(ordall(self._map), cache_file)
                cache_file.close()
        except IOError:
            sys.stderr.write("warning: failed to write cache.\n")
        except Exception:
            sys.stderr.write("warning: unexpected error writing cache.\n")

    @classmethod
    def load(cls, filename=PICKLE_CACHE_PATH):
        from pickle import UnpicklingError
        from pickle import load as pickle_load

        try:
            with open(filename, "rb") as cache_file:
                map_ = unordall(pickle_load(cache_file))
                return cls(map_)
        except UnpicklingError:
            sys.stderr.write("warning: failed to read cache file.\n")
            raise
        except IOError:
            sys.stderr.write("note: cache file not found.\n")
            raise
        except:
            sys.stderr.write("warning: unexpected error loading cache.\n")
            raise


class SqliteCache(Cache):
    def __init__(self, db=None):
        super(SqliteCache, self).__init__(None)
        self.db = db

    def get(self, key):
        cursor = self.db.cursor()
        cursor.execute("SELECT txt FROM tex2txt WHERE tex = ?", (key,))
        row = cursor.fetchone()
        cursor.close()
        if row is None:
            return None
        else:
            return row[0]

    def set(self, key, value):
        cursor = self.db.cursor()
        cursor.execute("INSERT OR REPLACE INTO tex2txt VALUES (?,?)", (key, value))
        self.db.commit()
        cursor.close()

    def save(self):
        self.db.close()
        self.db = None

    @classmethod
    def load(cls, filename=SQLITE_CACHE_PATH):
        import sqlite3

        db = sqlite3.connect(filename, timeout=SQLITE_TIMEOUT)
        # make sure the map table exists
        cursor = db.cursor()
        cursor.execute(
            "CREATE TABLE IF NOT EXISTS" "  tex2txt(tex TEXT PRIMARY KEY, txt TEXT)"
        )
        db.commit()
        cursor.close()
        return cls(db)


def get_cache(cls=SqliteCache):
    try:
        return cls.load()
    except Exception as e:
        sys.stderr.write("Warning: %s load failed: %s\n" % (str(cls), str(e)))
        return cls()


def tex_compile(fn):
    """
    Invokes tex to compile the file with the given name.
    Returns the name of the output file (.dvi), the empty string if
    the name could not be determined, or None if compilation fails.
    """

    from subprocess import PIPE, Popen

    cmd = TEX_COMMAND + " " + "-output-directory=" + TEX_OUTPUTDIR + " " + fn

    try:
        # TODO: avoid shell with Popen
        tex = Popen(cmd, shell=True, stdin=None, stdout=PIPE, stderr=PIPE)
        tex.wait()
        tex_out, tex_err = tex.communicate()

        # check tex output to determine output file name or to see
        # if an error message indicating nothing was output is
        # included.
        dvifn, no_output = "", False
        for ll in tex_out.decode("utf-8").split("\n"):
            m = re.match(r"Output written on (\S+)", ll)
            if m:
                dvifn = m.group(1)
            if "No pages of output" in ll:
                no_output = True

        if no_output and not dvifn:
            # print >> sys.stderr, "rewritetex: failed to compile tex"
            error_lines = [
                ll for ll in tex_out.decode("utf-8").split("\n") if "Error" in ll
            ]
            if error_lines:
                sys.stderr.write("\n".join(error_lines))
            return None

        return dvifn
    except IOError:
        # print >> sys.stderr, "rewritetex: error compiling tex document!"
        return None


def run_catdvi(fn):
    """
    Invokes catdvi to get the text content of the given .dvi file.
    Returns catdvi output or None if the invocation fails.
    """

    from subprocess import PIPE, Popen

    cmd = CATDVI_COMMAND + " " + fn

    try:
        # TODO: avoid shell with Popen
        catdvi = Popen(cmd, shell=True, stdin=None, stdout=PIPE, stderr=PIPE)
        catdvi.wait()
        catdvi_out, catdvi_err = catdvi.communicate()
        return catdvi_out
    except IOError as e:
        sys.stderr.write("rewritetex: failed to invoke catdvi:\n", e)
        return None


def tex2str(tex):
    """
    Given a tex document as a string, returns a text string
    approximating the tex content. Performs conversion using the
    external tools tex and catdvi.
    """

    from tempfile import NamedTemporaryFile

    # perform some minor tweaks to the given tex document to get
    # around compilation problems that frequently arise with PMC
    # NXML embedded tex:

    # remove "\usepackage{pmc}". It's not clear what the contents
    # of this package are (I have not been able to find it), but
    # compilation more often succeeds without it than with it.
    tex = tex.replace("\\usepackage{pmc}", "")

    # replace "\documentclass{minimal}" with "\documentclass{slides}".
    # It's not clear why, but some font commands (e.g. "\tt") appear
    # to fail with the former.
    tex = re.sub(r"(\\documentclass(?:\[[^\[\]]*\])?\{)minimal(\})", r"\1slides\2", tex)

    # now ready to try conversion.

    # create a temporary file for the tex content
    try:
        with NamedTemporaryFile("w", suffix=".tex") as tex_tmp:
            tex_tmp.write(tex)
            tex_tmp.flush()

            tex_out_fn = tex_compile(tex_tmp.name)

            if tex_out_fn is None:
                # failed to compile
                sys.stderr.write(
                    'rewritetex: failed to compile tex document:\n"""\n%s\n"""' % tex
                )
                return None

            # if no output file name could be found in tex output
            # in the expected format, back off to an expected default
            if tex_out_fn == "":
                expected_out_fn = tex_tmp.name.replace(".tex", ".dvi")
                tex_out_fn = os.path.join(
                    TEX_OUTPUTDIR, os.path.basename(expected_out_fn)
                )

            dvistr = run_catdvi(tex_out_fn)

            try:
                dvistr = dvistr.decode(INPUT_ENCODING)
            except UnicodeDecodeError:
                sys.stderr.write(
                    "rewritetex: error decoding catdvi output as %s (adjust INPUT_ENCODING?)\n"
                    % INPUT_ENCODING
                )

            if dvistr is None or dvistr == "":
                sys.stderr.write(
                    "rewritetex: likely error invoking catdvi (empty output)\n"
                )
                return None

            # perform minor whitespace cleanup
            dvistr = re.sub(r"\s+", " ", dvistr)
            dvistr = re.sub(r"^\s+", "", dvistr)
            dvistr = re.sub(r"\s+$", "", dvistr)

            return dvistr
    except IOError:
        sys.stderr.write("rewritetex: failed to create temporary file\n")
        raise


def rewrite_tex_element(e, s):
    """
    Given an XML tree element e and a string s, stores the original
    text content of the element in an attribute and replaces it with
    the string, further changing the tag to relect the change.
    """

    # check that the attributes that will be used don't clobber
    # anything
    for a in (ORIG_TAG_ATTRIBUTE, ORIG_TEXT_ATTRIBUTE):
        assert a not in e.attrib, (
            "rewritetex: error: attribute '%s' already defined!" % a
        )

    # store original text content and tag as attributes
    e.attrib[ORIG_TEXT_ATTRIBUTE] = e.text
    e.attrib[ORIG_TAG_ATTRIBUTE] = e.tag

    # swap in the new ones
    e.text = s
    e.tag = REWRITTEN_TAG

    # that's all
    return True


class Stats(object):
    def __init__(self):
        self.rewrites = 0
        self.cache_hits = 0
        self.cache_misses = 0
        self.conversions_ok = 0
        self.conversions_err = 0

    def zero(self):
        return (
            self.rewrites == 0
            and self.cache_hits == 0
            and self.cache_misses == 0
            and self.conversions_ok == 0
            and self.conversions_err == 0
        )

    def __str__(self):
        return "%d rewrites (%d cache hits, %d misses; converted %d, failed %d)" % (
            self.rewrites,
            self.cache_hits,
            self.cache_misses,
            self.conversions_ok,
            self.conversions_err,
        )


def process_tree(tree, cache=None, stats=None, options=None):
    if cache is None:
        cache = get_cache()
    if stats is None:
        stats = Stats()

    root = tree.getroot()

    # find "tex-math" elements in any namespace ("local-name")
    # anywhere in the tree.
    for e in root.xpath("//*[local-name()='tex-math']"):
        tex = e.text

        # normalize the tex document for cache lookup
        tex_norm = normalize_tex(tex)

        mapped = cache.get(tex_norm)

        if mapped is not None:
            stats.cache_hits += 1
        else:
            stats.cache_misses += 1

            # no existing mapping to string; try to convert
            s = tex2str(tex)

            # only use results of successful conversions
            if s is None or s == "":
                mapped = None
                stats.conversions_err += 1
            else:
                stats.conversions_ok += 1
                mapped = s
                cache.set(tex_norm, s)

        if mapped is not None:
            # replace the <tex-math> element with the mapped text
            rewrite_tex_element(e, mapped)
            stats.rewrites += 1

    return tree


def read_tree(filename):
    try:
        return ET.parse(filename)
    except ET.XMLSyntaxError:
        sys.stderr.write("Error parsing %s\n" % filename)
        raise


def write_tree(tree, fn, options=None):
    if options is not None and options.stdout:
        tree.write(sys.stdout, encoding=OUTPUT_ENCODING)
        return True

    if options is not None and options.directory is not None:
        output_dir = options.directory
    else:
        output_dir = ""

    output_fn = os.path.join(output_dir, os.path.basename(fn))

    # TODO: better checking to protect against clobbering.
    # if output_fn == fn and (not options or not options.overwrite):
    #    print >> sys.stderr, 'rewritetex: skipping output for %s: file would overwrite input (consider -d and -o options)' % fn
    # else:
    # OK to write output_fn
    try:
        with open(output_fn, "w") as of:
            tree.write(of, encoding=OUTPUT_ENCODING)
    except IOError as ex:
        sys.stderr.write("rewritetex: failed write: %s\n" % ex)

    return True


def process(fn, cache=None, stats=None, options=None):
    tree = read_tree(fn)
    process_tree(tree)
    write_tree(tree, fn, options)


def argparser():
    import argparse

    ap = argparse.ArgumentParser(
        description="Rewrite <tex-math> element content with approximately equivalent text strings in PMC NXML files."
    )
    ap.add_argument(
        "-d", "--directory", default=None, metavar="DIR", help="output directory"
    )
    ap.add_argument(
        "-o",
        "--overwrite",
        default=False,
        action="store_true",
        help="allow output to overwrite input files",
    )
    ap.add_argument(
        "-s", "--stdout", default=False, action="store_true", help="output to stdout"
    )
    ap.add_argument(
        "-v", "--verbose", default=False, action="store_true", help="verbose output"
    )
    ap.add_argument("file", nargs="+", help="input PubMed Central NXML file")
    return ap


def main(argv):
    options = argparser().parse_args(argv[1:])
    stats = Stats()
    cache = get_cache()

    for fn in options.file:
        process(fn, cache, stats, options)

    cache.save()

    if options.verbose and not stats.zero():
        sys.stderr.write("rewritetex: %s\n" % str(stats))

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
