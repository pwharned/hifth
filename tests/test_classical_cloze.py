from pathlib import Path

from scripts.classical_cloze.pipeline import (
    Candidate,
    Sentence,
    Token,
    cloze_text,
    find_candidates,
    metadata_for,
    normalize,
    read_conllu,
    source_terms,
    unmatched_status,
)
from scripts.classical_cloze.translations import canonical_citation, lookup, passage_url


def test_normalization_preserves_display_but_supports_lookup_variants():
    assert normalize("ἄνθρωπος", "greek", loose=True) == "ανθρωποσ"
    assert normalize("Jūlius", "latin", loose=True) == "iulius"
    assert normalize("VIVIT", "latin") == "uiuit"
    assert normalize("dēfendō", "latin", loose=True) == "defendo"


def test_dictionary_front_extracts_explicit_headword():
    assert source_terms("sto, stare, steti, status", "latin") == ["sto"]
    assert source_terms("dēnique (adv.)", "latin") == ["dēnique"]
    assert source_terms("πολίχνιον, τό, Dim.", "greek") == ["πολίχνιον"]
    assert source_terms("πάνυ γε, πάνυ μὲν οὖν", "greek") == ["πάνυ", "γε"]


def test_unmatched_failure_classification():
    assert unmatched_status("tinguo tinguere tinxi tinctum", "latin") == "unmatched_phrase"
    assert unmatched_status("dēnique (unusual note)", "latin") == "annotation_unknown"
    assert unmatched_status("an English grammar explanation with many words that does not represent a concise Latin lexical target at all", "latin") == "unmatched_long_text"


def test_metadata_excludes_ineligible_sources():
    assert metadata_for("greek", "UD_Ancient_Greek-PROIEL", {"source": "The Greek New Testament, John 1"}) is None
    assert metadata_for("latin", "UD_Latin-PROIEL", {"source": "Jerome's Vulgate, Matthew 1"}) is None
    assert metadata_for("greek", "UD_Ancient_Greek-PROIEL", {"source": "Histories, Book 1, chapter 1"}) == (
        "Herodotus", "Histories", "classical"
    )


def test_conllu_parser_and_phrase_match(tmp_path: Path):
    corpus = tmp_path / "UD_Latin-Perseus"
    corpus.mkdir()
    path = corpus / "la_perseus-ud-test.conllu"
    path.write_text(
        "# newdoc id = phi0690.phi003.perseus-lat1.tb.xml\n"
        "# sent_id = test@1\n"
        "# text = Arma virumque cano.\n"
        "1\tArma\tarmo\tNOUN\t_\tCase=Acc|Number=Plur\t3\tobj\t_\t_\n"
        "2\tvirumque\tvir\tNOUN\t_\tCase=Acc|Number=Sing\t3\tobj\t_\t_\n"
        "3\tcano\tcano\tVERB\t_\tMood=Ind|Person=1\t0\troot\t_\tSpaceAfter=No\n"
        "4\t.\t.\tPUNCT\t_\t_\t3\tpunct\t_\tSpaceAfter=No\n\n",
        encoding="utf-8",
    )
    sentences = list(read_conllu(path, "latin"))
    assert sentences[0].author == "Virgil"
    lemma = {("latin", "cano"): [(sentences[0], 2)]}
    form = {("latin", "arma"): [(sentences[0], 0)]}
    candidates, error = find_candidates("cano", "latin", lemma, form)
    assert error is None
    assert cloze_text(candidates[0]) == "Arma virumque {{c1::cano}}."


def test_cloze_preserves_spacing_around_target():
    sentence = Sentence(
        "latin", "Virgil", "Aeneid", "classical", "test", "doc", "1",
        "Arma virumque cano.",
        (
            Token("Arma", "arma", "NOUN", {}),
            Token("virumque", "vir", "NOUN", {}),
            Token("cano", "cano", "VERB", {}, False),
            Token(".", ".", "PUNCT", {}, False),
        ),
    )
    assert cloze_text(Candidate(sentence, 1, 2, "lemma")) == "Arma {{c1::virumque}} cano."


def test_perseus_translation_uses_matching_sentence_in_canonical_passage(tmp_path: Path):
    urn = "phi0448.phi001.perseus-eng2"
    (tmp_path / f"{urn}.xml").write_text(
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader><fileDesc><titleStmt>'
        '<editor role="translator">W. A. McDevitte</editor></titleStmt></fileDesc></teiHeader>'
        '<text><body><div n="1"><div n="1"><div n="1"><p>First sentence. '
        'Second sentence.</p></div></div></div></body></text></TEI>', encoding="utf-8",
    )
    result = lookup(tmp_path, "phi0448.phi001.perseus-lat2", "1.1.1.2")
    assert result is not None
    assert result.text == "Second sentence."
    assert result.level == "sentence"
    assert result.translator == "W. A. McDevitte"


def test_perseus_translation_extracts_stephanus_segment(tmp_path: Path):
    urn = "tlg0059.tlg003.perseus-eng2"
    (tmp_path / f"{urn}.xml").write_text(
        '<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body><div>'
        '<milestone unit="section" n="43a"/><p>Exact passage.</p>'
        '<milestone unit="section" n="43b"/><p>Next passage.</p>'
        '</div></body></text></TEI>', encoding="utf-8",
    )
    result = lookup(tmp_path, "0059-003", "43a")
    assert result is not None
    assert result.text == "Exact passage."
    assert result.level == "passage"


def test_perseus_link_is_a_complete_cts_urn():
    assert passage_url("tlg0059.tlg003.perseus-eng2", "43a") == (
        "https://scaife.perseus.org/reader/urn:cts:greekLit:"
        "tlg0059.tlg003.perseus-eng2:43a/"
    )
    assert canonical_citation("phi0690.phi003.perseus-lat2", "1.1-4.1") == "1.1-4"
    assert canonical_citation("phi0448.phi001.perseus-lat2", "1.1.1.2") == "1.1.1"
