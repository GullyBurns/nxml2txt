from bs4 import BeautifulSoup, Tag
from collections import namedtuple
import dataclasses
import html
from lxml import etree
from lxml.etree import ElementTree
from nxml2txt import rewritetex
from nxml2txt import rewritemmla
from nxml2txt import rewriteu2a
from nxml2txt import standoff
import os
import pandas as pd
import re
import requests
import sys

TexOptions = namedtuple("TexOptions", "verbose")
U2aOptions = namedtuple("U2aOptions", "hex keep_missing stdout directory overwrite")

@dataclasses.dataclass
class NxmlDoc:
    """A class that provides structure for full text papers specified under the JATS 'nxml' format.
    The class provides methods to extract and manipulate the text and metadata of the papers.

    Note that this class has a dependency on `https://github.com/GullyBurns/nxml2txt`
    (which is code that extracts text + structure from JATS XML files into a text + standoff annotation list
    that we can use to see to track nested structure of the text).

    Attributes:
    xml: str
        The XML content of the file
    ft_id: str
        The identifier of the paper
    text: str
        Plain text of the paper's contents
    standoffs: str
        Standoff annotations superimposed over the prompts

    Methods:
    get_figure_reference(t)
        Returns the identifier of the figure referenced in the text
    get_sec_tree(t)
        Returns the section tree of the text
    get_sec_tag(t)
        Returns the section tag of the text
    get_top_level_sec_tag(t)
        Returns the top level section tag of the text
    generate_tag_tree(t)
        Returns the tag tree of the text
    list_section_titles()
        Returns the list of section titles
    search_section_titles(query)
        Searches for section titles that match the query
    read_section_text(t)
        Returns the text of the section
    build_simple_document_dataframe()
        Builds a simple dataframe of the document
    build_enhanced_document_dataframe()
        Builds an enhanced dataframe of the document
    extract_ref_dict_from_nxml(search_pubmed=False)
        Extracts the reference dictionary from the nxml file
    """

    # The XML content of the file
    xml: str

    # The identifier of the paper
    ft_id: str

    # Plain text of the paper's contents
    text: str

    # Standoff annotations superimposed over the  prompts
    standoffs: str

    def __init__(self, ft_id, xml):
        self.ft_id = ft_id

        # HTML entities kill the XML parse
        # but any '<' characters must be replaced with &lt; in XML (and '& with &amp;)
        xml = xml.replace("<", "__less_than__")
        xml = html.unescape(xml)
        xml = xml.replace("&", "&amp;")
        xml = xml.replace("<", "&lt;")
        xml = xml.replace("__less_than__", "<")
        xml = xml.encode("utf-8")
        self.xml = xml

        tree = ElementTree(etree.fromstring(xml))
        tex_options = TexOptions(verbose=True)
        rewritetex.process_tree(tree, options=tex_options)

        # process MathML annotations
        rewritemmla.process_tree(tree)

        # normalize whitespace
        # respace.process_tree(tree)

        # map unicodoffs = nxml2txt(nxmlfne to ASCII)
        u2a_options = U2aOptions(
            keep_missing=True, hex=False, stdout=False, directory=None, overwrite=False
        )
        rewriteu2a.process_tree(tree, options=u2a_options)

        # convert to text and standoffs
        text, standoffs = standoff.convert_tree(tree)
        self.text = text
        self.standoffs = standoffs

        self.to_exclude = ["table-wrap-foot"]
        self.text_tag_types = [
            "front/article-title",
            "front/abstract",
            "body/p",
            "body/title",
            "body/label",
            "back/p",
            "back/title",
        ]
        self.section_tag_types = ["front/article-title", "front/abstract", "body/sec"]

        self.tag_types = {
            "text": ["article-title", "abstract", "p", "title", "label", "caption"],
            "structure": [
                "front",
                "body",
                "back",
                "ref-list",
                "sec",
                "fig",
                "supplementary-material",
            ],
            "xref": [
                "xref",
                "ref",
                "label",
                "name",
                "surname",
                "year",
                "pub-id",
                "fpage",
            ],
        }
        # Define a namespace dictionary
        self.namespaces = {
            'nlm': 'http://dtd.nlm.nih.gov/2.0/xsd/archivearticle'
        }

    def get_figure_reference(self, t):
        pos = t.start
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if pos >= s.start and pos < s.end and s != t:
                hits.append(s)
        for t in hits:
            if t.element.tag == "fig":
                return t.element.get("id", "")
        return ""

    def get_sec_tree(self, t):
        pos = t.start
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if pos >= s.start and pos < s.end and s != t:
                hits.append(s)
        sec_tree = ""
        for t in hits:
            if t.element.tag == "sec":
                if len(sec_tree) > 0:
                    sec_tree += " >> "
                if (
                    t.element.find("title") is not None
                    and t.element.find("title").text is not None
                ):
                    sec_tree += t.element.find("title").text
                else:
                    sec_tree += " ??? "
        return sec_tree

    def get_sec_tag(self, t):
        pos = t.start
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if pos >= s.start and pos < s.end and s != t:
                hits.append(s)
        sec = None
        for t in hits:
            if t.element.tag == "sec":
                sec = t
        if sec is None:
            return ""
        elif sec.element.find("title") is not None:
            return sec.element.find("title").text
        else:
            return ""

    def get_top_level_sec_tag(self, t):
        pos = t.start
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if pos >= s.start and pos < s.end and s != t:
                hits.append(s)
        for t in hits:
            if t.element.tag == "sec":
                if t.element.get("sec-type", None):
                    return t.element.get("sec-type")
                elif t.element.find("title") is not None:
                    return t.element.find("title").text
        return ""

    def generate_tag_tree(self, t):
        pos = t.start
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if pos >= s.start and pos < s.end and s != t:
                hits.append(s)
        # tag_tree = '|'.join(['%s[%s...]'%(t.element.tag,self.text[t.start:t.start+8]) if t.element.tag=='sec' else t.element.tag for t in hits])
        tag_tree = "|".join([t.element.tag for t in hits])
        tag_tree = tag_tree + "." + t.element.tag
        return tag_tree

    def list_section_titles(self):
        secs = [t for t in self.standoffs if t.element.tag == "sec"]
        titles = [t.element.find("title").text.strip() for t in secs]
        levels = [self.generate_tag_tree(t).count("|") for t in secs]
        return (titles, levels)

    def search_section_titles(self, query):
        standoffs = []
        for s in self.standoffs:
            if s.element.tag == "sec":
                title_tag = s.element.find("title")
                if (
                    title_tag and title_tag.text and query in title_tag.text.lower()
                ) or query in s.element.get("sec-type", ""):
                    standoffs.append(s)
        return standoffs

    def read_section_text(self, t):
        hits = []
        for s in sorted(self.standoffs, key=lambda x: x.start):
            if (
                s.start > t.start
                and s.end < t.end
                and s != t
                and (s.element.tag == "p" or s.element.tag == "title")
            ):
                hits.append(s)
        return "\n".join([re.sub("\s+", " ", self.text[t.start : t.end]) for t in hits])

    def build_simple_document_dataframe(self):
        text_tuples = []

        try:
            # two stage process - build a lookup list of all relevant tags
            # - then use the tags start/end properties to identify text portions of the paper and render those.

            this_doc_standoffs = {
                t: [] for tt in self.tag_types.keys() for t in self.tag_types[tt]
            }

            all_xrefs = []
            for s in self.standoffs:
                if this_doc_standoffs.get(s.element.tag) is not None:
                    this_doc_standoffs.get(s.element.tag).append(s)
                if s.element.tag == "xref":
                    all_xrefs.append(s)
            #
            # skip the whole file if there's no body tag.
            #
            if len(this_doc_standoffs.get("body")) == 0:
                return None

            text_so_list = []
            for ttt in self.text_tag_types:
                part, tag = ttt.split("/")
                if len(this_doc_standoffs.get(part)) == 0:
                    continue
                part_so = this_doc_standoffs.get(part)[0]
                for so in this_doc_standoffs.get(tag):
                    if so.start < part_so.start or so.end > part_so.end:
                        continue
                    text_so_list.append(so)
                    # print((row.PMID, local_id, so.element.tag, query_document_standoffs(so, text, standoffs), so.start, (so.end-so.start), text[so.start:so.end]))

            # Manipulate standoff annotations so that titles and labels fall naturally in the text
            # and paragraph tags that hold other paragraphs (as is the case with pmid:26791617) don't trigger repeating text.
            # Make sure the SOs only tile the document and do not overlap.
            text_so_list = sorted(text_so_list, key=lambda x: x.start)
            last_so = None
            for so in text_so_list:
                if last_so:
                    if last_so.end > so.start:
                        last_so.end = so.start - 1
                last_so = so

            for local_id, so in enumerate(text_so_list):
                sec_tree = self.get_sec_tree(so)
                sec_title = self.get_sec_tag(so)
                top_sec_title = self.get_top_level_sec_tag(so)
                figure_reference = self.get_figure_reference(so)
                so_text = self.text[so.start : so.end]
                if so.element.tag == "article-title" or so.element.tag == "abstract":
                    sec_title = "TIAB"
                    top_sec_title = "TIAB"
                    sec_tree = "TIAB"
                tuple = (
                    self.ft_id,
                    local_id,
                    so.element.tag,
                    top_sec_title,
                    sec_tree,
                    sec_title,
                    so.start,
                    (so.end - so.start),
                    figure_reference,
                    so_text,
                )
                text_tuples.append(tuple)

        except etree.XMLSyntaxError as xmlErr:
            print("XML Syntax Error: {0}".format(xmlErr))
        except UnicodeDecodeError as unicodeErr:
            print("Unicode parsing Error: {0}".format(unicodeErr))
        # except TypeError as typeErr:
        #  print("Type Error: {0}".format(typeErr))
        #    print("ValueError: {0}".format(valErr))
        #    return None

        text_df = pd.DataFrame(
            text_tuples,
            columns=[
                "PMID",
                "PARAGRAPH_ID",
                "TAG",
                "TOP_SECTION",
                "SECTION_TREE",
                "SECTION",
                "OFFSET",
                "LENGTH",
                "FIG_REF",
                "PLAIN_TEXT",
            ],
        )
        return text_df

    def build_enhanced_document_dataframe(self):
        """This method processes the JATS file and returns a dataframe with the following columns:
        PMID: the paper's identifier
        PARAGRAPH_ID: the paragraph's identifier
        TAG: the tag type
        TAG_TREE: the tag tree
        OFFSET: the offset of the text in the overall document
                (note that these values are derived from the text generated by
                 the nxml2txt package - which differs from the text we are generating
                 here).
        LENGTH: the length of the text
        FIG_REF: Whether the text is a reference to a figure (or table)
        PLAIN_TEXT: the text itself
        """

        text_tuples = []

        try:
            # two stage process - build a lookup list of all relevant tags
            # - then use the tags start/end properties to identify text portions of the paper and render those.

            this_doc_standoffs = {
                t: [] for tt in self.tag_types.keys() for t in self.tag_types[tt]
            }

            all_xrefs = []
            for s in self.standoffs:
                # temporary hack to deal with namespaces. 
                t = re.sub('\{.*?\}', '', s.element.tag)
                if this_doc_standoffs.get(t) is not None:
                    this_doc_standoffs.get(t).append(s)
                if t == "xref":
                    all_xrefs.append(s)
            #
            # skip the whole file if there's no body tag.
            #
            if len(this_doc_standoffs.get("body")) == 0:
                return None

            ref_dict = self.extract_ref_dict_from_nxml()

            text_so_list = []
            for ttt in self.text_tag_types:
                part, tag = ttt.split("/")
                part_so = this_doc_standoffs.get(part)[0]
                for so in this_doc_standoffs.get(tag):
                    if so.start < part_so.start or so.end > part_so.end:
                        continue
                    text_so_list.append(so)
                    # print((row.PMID, local_id, so.element.tag, query_document_standoffs(so, text, standoffs), so.start, (so.end-so.start), text[so.start:so.end]))

            # Manipulate standoff annotations so that titles and labels fall naturally in the text
            # and paragraph tags that hold other paragraphs (as is the case with pmid:26791617) don't trigger repeating text.
            # Make sure the SOs only tile the document and do not overlap.
            text_so_list = sorted(text_so_list, key=lambda x: x.start)
            last_so = None
            for so in text_so_list:
                if last_so:
                    if last_so.end > so.start:
                        last_so.end = so.start - 1
                last_so = so

            for local_id, so in enumerate(text_so_list):
                sec_tree = self.get_sec_tree(so)
                sec_title = self.get_sec_tag(so)
                top_sec_title = self.get_top_level_sec_tag(so)
                figure_reference = self.get_figure_reference(so)

                # ANY EXCLUSION CRITERIA FOR TAGS PUT IT HERE

                # SEARCH FOR XREFS IN THIS TEXT BLOCK - AND SUB THEM INTO THE TEXT.
                so_text = ""
                prev_end = so.start
                ref_xrefs = [
                    x
                    for x in all_xrefs
                    if x.start >= so.start
                    and x.end <= so.end
                    and x.element.attrib["ref-type"] == "bibr"
                ]
                # print(ref_xrefs)

                if len(ref_xrefs) > 0:
                    refbib_xrefs = [
                        x
                        for x in all_xrefs
                        if x.start >= so.start
                        and x.end <= so.end
                        and (
                            x.element.attrib["ref-type"] == "bibr"
                            or x.element.attrib["ref-type"] == "fig"
                        )
                    ]
                    for x in refbib_xrefs:
                        if x.element.attrib["ref-type"] == "bibr":
                            ref_id = x.element.attrib["rid"]
                            ref = ref_dict.get(ref_id, None)
                            if ref and ref.get("pmid"):
                                ref_text = "<<REF:%s>>" % (ref.get("pmid"))
                            elif ref:
                                ref_text = "<<REF:%s-%s-%s-%s>>" % (
                                    ref.get("first_author", "???"),
                                    ref.get("year", "?"),
                                    ref.get("vol", "?"),
                                    ref.get("page", "?"),
                                )
                            else:
                                ref_text = "<<REF>>"
                            so_text += self.text[prev_end : x.start] + ref_text
                        else:
                            fig_id = x.element.attrib["rid"]
                            fig_text = "%s <<FIG:%s>>" % (
                                self.text[x.start : x.end],
                                fig_id,
                            )
                            so_text += self.text[prev_end : x.start] + fig_text
                        # print(pmid, ref_id, ref_text)
                        prev_end = x.end

                    # if len(so_text)>0:
                    #  print(so_text)
                    so_text += self.text[prev_end : so.end]
                    so_text = html.unescape(so_text)
                # __________________________________________________________________
                else:  # USE REGEXES TO SUBSTITUTE REFERENCES IN PASSAGE TEXT
                    fig_xrefs = [
                        x
                        for x in all_xrefs
                        if x.start >= so.start
                        and x.end <= so.end
                        and x.element.attrib["ref-type"] == "fig"
                    ]
                    so_text = ""
                    prev_end = so.start
                    for x in fig_xrefs:
                        fig_id = x.element.attrib["rid"]
                        fig_text = "%s <<FIG:%s>>" % (
                            self.text[x.start : x.end],
                            fig_id,
                        )
                        so_text += self.text[prev_end : x.start] + fig_text
                        prev_end = x.end
                    so_text += self.text[prev_end : so.end]
                    so_text = html.unescape(so_text)

                    # print(so_text)
                    for key in ref_dict:
                        ref = ref_dict[key]
                        if ref.get("pmid"):
                            ref_text = " <<REF:%s>> " % (ref.get("pmid"))
                        else:
                            ref_text = " <<REF:%s-%s-%s-%s>> " % (
                                ref.get("first_author", "???"),
                                ref.get("year", "?"),
                                ref.get("vol", "?"),
                                ref.get("page", "?"),
                            )
                        if ref.get("year") and ref.get("second_author"):
                            regex = (
                                "%s( and %s,|,|\\s+et al\\.\\,|\\s+et al){0,1}\\s+%s"
                                % (
                                    ref.get("first_author", ""),
                                    ref.get("second_author", ""),
                                    ref.get("year", ""),
                                )
                            )
                        elif ref.get("year") and len(ref.get("first_author", "")) > 0:
                            regex = "%s(,|\\s+et al\\.\\,|\\s+et al){0,1}\\s+%s" % (
                                ref.get("first_author", ""),
                                ref.get("year", ""),
                            )
                        else:
                            regex = (
                                "%s( and [A-Za-z]+|,|\\s+et al\\.\\,|\\s+et al){0,1}\\s+(19|20)[0-9][0-9]"
                                % (ref.get("first_author", ""))
                            )
                        pattern = re.compile(regex)
                        if pattern.search(so_text):
                            so_text = pattern.sub(ref_text, so_text)
                        # print( pattern.sub(ref_text,so_text))

                tuple = (
                    self.ft_id,
                    local_id,
                    re.sub('\{.*?\}', '', so.element.tag),
                    top_sec_title,
                    sec_tree,
                    sec_title,
                    so.start,
                    (so.end - so.start),
                    figure_reference,
                    so_text,
                )
                text_tuples.append(tuple)

        except etree.XMLSyntaxError as xmlErr:
            print("XML Syntax Error: {0}".format(xmlErr))
        except UnicodeDecodeError as unicodeErr:
            print("Unicode parsing Error: {0}".format(unicodeErr))

        text_df = pd.DataFrame(
            text_tuples,
            columns=[
                "PMID",
                "PARAGRAPH_ID",
                "TAG",
                "TOP_SECTION",
                "SECTION_TREE",
                "SECTION",
                "OFFSET",
                "LENGTH",
                "FIG_REF",
                "PLAIN_TEXT",
            ],
        )
        return text_df

    def extract_ref_dict_from_nxml(self, search_pubmed=False):
        if self.xml is None:
            return

        soup = BeautifulSoup(self.xml, "lxml-xml")

        references = soup.find_all("ref")
        all_ref_dict = {}
        for r in references:
            ref_dict = {}
            ref_dict["ref"] = r.attrs.get("id")

            ref_dict["author"] = ""
            for t in r.descendants:
                if (
                    type(t) is Tag
                    and t.name == "surname"
                    and ref_dict.get("first_author", None) is None
                ):
                    ref_dict["first_author"] = re.sub("'", "''", t.text)
                if (
                    type(t) is Tag
                    and t.name == "surname"
                    and ref_dict.get("first_author", None) is not None
                ):
                    ref_dict["second_author"] = re.sub("'", "''", t.text)
                if (
                    type(t) is Tag
                    and t.name == "name"
                    and len(ref_dict.get("author")) > 0
                ):
                    ref_dict["author"] += ", "
                if type(t) is Tag and t.name == "surname":
                    ref_dict["author"] += re.sub("'", "''", t.text)
                if type(t) is Tag and t.name == "given-names":
                    ref_dict["author"] += " " + re.sub("'", "''", t.text)
                elif type(t) is Tag and t.name == "article-title":
                    ref_dict["title"] = re.sub("'", "''", t.text)
                elif type(t) is Tag and t.name == "source":
                    ref_dict["journal"] = t.text
                elif type(t) is Tag and t.name == "year":
                    m = re.match("(\\d\\d\\d\\d)", t.text)
                    if m:
                        ref_dict["year"] = m.group(1)
                elif type(t) is Tag and t.name == "volume":
                    ref_dict["vol"] = t.text
                elif type(t) is Tag and t.name == "fpage":
                    ref_dict["page"] = t.text

            all_ref_dict[ref_dict.get("ref")] = ref_dict

        # Search pubmed for the PMIDs
        if search_pubmed:
            if os.environ.get("NCBI_API_KEY") is None:
                raise Exception(
                    "Error attempting to query NCBI for URL data, did you set the NCBI_API_KEY environment variable?"
                )
            pubmed_api_key = os.environ.get("NCBI_API_KEY")

            clauses = []
            for r in all_ref_dict:
                ref_dict = all_ref_dict[r]
                if (
                    ref_dict.get("first_author", None) is not None
                    and ref_dict.get("year", None) is not None
                    and ref_dict.get("vol", None) is not None
                    and ref_dict.get("page", None) is not None
                ):
                    c = "(%s[au]+AND+%s[dp]+AND+%s[vi]+AND+%s[pg]')" % (
                        ref_dict.get("first_author"),
                        ref_dict.get("year"),
                        ref_dict.get("vol"),
                        ref_dict.get("page"),
                    )
                    clauses.append(c)

            if len(clauses) == 0:
                return all_ref_dict

            stem1 = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&api_key="
                + pubmed_api_key
            )
            pmids = []
            for i in range(0, len(clauses), 50):
                query1 = "+OR+".join(clauses[i : i + 50])
                r1 = requests.get(stem1 + "&db=pubmed&term=" + query1)
                soup2 = BeautifulSoup(r1.text, "lxml-xml")

                for id in soup2.find_all("id"):
                    pmids.append(id.text)

            stem2 = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&api_key="
                + pubmed_api_key
            )
            for i in range(0, len(pmids), 50):
                query2 = ",".join(clauses[i : i + 50])
                r2 = requests.get(stem2 + "&db=pubmed&id=" + query2)
                soup2 = BeautifulSoup(r2.text, "lxml-xml")
                for article_tag in soup2.find_all("PubmedArticle"):
                    first_author = article_tag.find("Author").find("LastName").text
                    year = article_tag.find("PubDate").find("Year").text
                    vol = article_tag.find("Volume").text
                    page = article_tag.find("StartPage").text
                    pmid = article_tag.find("PMID").text
                    all_ref_dict[
                        ("%s-%s-%s-%s" % (first_author, year, vol, page)).lower()
                    ] = pmid

        return all_ref_dict


def main(argv):
    usage = "%s NXMLFILE_PATH TSV_PATH" % __file__
    if len(argv) > 3 :
        sys.stderr.write("Usage: %s\n" % usage)
        return 1

    nxml_file_path = argv[1]
    df_file_path = argv[2]
    
    if os.path.exists(nxml_file_path):
        with open(nxml_file_path, "r") as f:
            doc_id = os.path.basename(nxml_file_path).replace(".nxml", "")
            xml = f.read()
            d = NxmlDoc(doc_id, xml)
            df = d.build_enhanced_document_dataframe()

        # Make sure the output directory exists
        if not os.path.exists(os.path.dirname(df_file_path)):
            os.makedirs(os.path.dirname(df_file_path))

        # Write the dataframe to a TSV file
        df.to_csv(df_file_path, sep="\t", index=False)
    
    else:
        sys.stderr.write("Input file not found: %s\n" % nxml_file_path)
        sys.stderr.write("Usage: %s\n" % usage)
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv))
