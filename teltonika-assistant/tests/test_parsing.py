from ingest.parsing import (clean_soup, extract_breadcrumb, make_chunks, model_from_breadcrumb,
                            split_sections)

HTML = """
<div class="mw-parser-output">
  <div class="breadcrumb"><span>Main Page &gt; Basic Trackers &gt; FMB920 &gt; FMB920 Manual &gt; FMB920 SMS/GPRS Commands</span></div>
  <p>SMS command structure:</p>
  <pre>&lt;login&gt;&lt;space&gt;&lt;password&gt;&lt;space&gt;&lt;command&gt;</pre>
  <div class="mw-heading mw-heading2"><h2>Common commands <span class="mw-editsection">[edit]</span></h2></div>
  <ul><li>getinfo<ul><li>nested item</li></ul></li><li>getver</li></ul>
  <table>
    <tr><th>Command</th><th>SMS</th><th>GPRS</th></tr>
    <tr><td>getinfo</td><td>Yes</td><td>Yes</td></tr>
    <tr><td>cpureset</td><td>Yes</td><td>Yes</td></tr>
  </table>
  <div class="mw-heading mw-heading3"><h3>Sensitive</h3></div>
  <p>setdigout may disable outputs.</p>
</div>
"""


def test_breadcrumb_and_model_detection():
    soup = clean_soup(HTML)
    bc = extract_breadcrumb(soup)
    assert bc[:3] == ["Main Page", "Basic Trackers", "FMB920"]
    assert model_from_breadcrumb(bc, {"FMB920"}) == "FMB920"
    assert model_from_breadcrumb(bc, {"FMB964"}) is None


def test_sections_keep_heading_path_and_ignore_edit_links():
    secs = split_sections(clean_soup(HTML), "FMB920 SMS/GPRS Commands")
    paths = [s.path for s in secs]
    assert ["FMB920 SMS/GPRS Commands", "Common commands"] in paths
    assert ["FMB920 SMS/GPRS Commands", "Common commands", "Sensitive"] in paths
    assert all("[edit]" not in " ".join(s.path) for s in secs)


def test_pre_block_preserved_literally():
    secs = split_sections(clean_soup(HTML), "T")
    assert "<login><space><password><space><command>" in secs[0].text


def test_table_kept_as_structured_rows():
    secs = split_sections(clean_soup(HTML), "T")
    tbl = [t for s in secs for t in s.tables][0]
    assert tbl.headers == ["Command", "SMS", "GPRS"]
    assert tbl.rows == [["getinfo", "Yes", "Yes"], ["cpureset", "Yes", "Yes"]]


def test_nested_list_items_not_duplicated():
    secs = split_sections(clean_soup(HTML), "T")
    text = "\n".join(s.text for s in secs)
    assert text.count("nested item") == 1


def test_chunks_respect_max_size():
    secs = split_sections(clean_soup(HTML), "T")
    chunks = make_chunks(secs, max_chars=60)
    assert chunks and all(len(c) < 400 for _, c in chunks)
