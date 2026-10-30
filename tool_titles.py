"""Human-readable titles and safety annotations for every tool on the endpoint.

The Claude connector directory requires each tool to carry a ``title`` and the
applicable ``readOnlyHint`` / ``destructiveHint``. Claude also uses the hint: a
read-only tool can run without per-call confirmation.

Every tool here searches or fetches public legal sources and changes nothing,
with one family of exceptions in the land-registry backend. Run locally
(stdio), ``tkgm_`` tools that produce a sketch, map, export, 3D view, Word sheet
or Excel table write that file to the user's disk, and ``tkgm_arayuze_yaz``
writes an answer into the local UI. Those are marked as not read-only and not
destructive (they add files, never delete or overwrite data). On the shared
HTTP endpoint the file-only tools are not listed and the others return their
output inline; the directory profile carries no ``tkgm_`` tool at all.

A tool missing from TITLES still gets a readable title from its name (see
``title_for``), so a new backend tool is never published without one;
``tests/test_profiles.py`` lists any that fell back, so the table stays complete.
"""

from __future__ import annotations

from typing import Any, Dict

# Shown before the action, so a title says which law it answers for.
PREFIX_LABELS = {
    "tr": "Türkiye", "nl": "Netherlands", "pl": "Poland", "at": "Austria",
    "ie": "Ireland", "fi": "Finland", "es": "Spain", "uk": "UK", "eu": "EU",
    "jp": "Japan", "gleif": "LEI", "tkgm": "Land registry", "az": "Azerbaijan",
    "scholar": "Scholarship", "contracts": "Contracts", "de": "Germany",
}

TITLES: Dict[str, str] = {
    # Türkiye
    "tr_ictihat_ara": "Türkiye: search case law",
    "tr_ictihat_getir": "Türkiye: get a court decision",
    "tr_ictihat_semantik_ara": "Türkiye: rank case law by meaning",
    "tr_aym_ara": "Türkiye: search Constitutional Court decisions",
    "tr_aym_getir": "Türkiye: get a Constitutional Court decision",
    "tr_uyusmazlik_ara": "Türkiye: search Court of Jurisdictional Disputes decisions",
    "tr_uyusmazlik_getir": "Türkiye: get a Court of Jurisdictional Disputes decision",
    "tr_mevzuat_ara": "Türkiye: search legislation",
    "tr_mevzuat_getir": "Türkiye: get legislation text",
    "tr_mevzuat_icindekiler": "Türkiye: legislation table of contents",
    "tr_mevzuat_madde_getir": "Türkiye: get a statutory article with citation",
    "tr_mevzuat_gerekce": "Türkiye: get legislative rationale",
    "tr_mevzuat_icinde_ara": "Türkiye: search within one piece of legislation",
    "tr_resmi_gazete_fihrist": "Türkiye: Official Gazette daily index",
    "tr_resmi_gazete_getir": "Türkiye: get an Official Gazette document",
    "tr_resmi_gazete_tara": "Türkiye: scan Official Gazette indexes by date range",
    "tr_kurum_karari_ara": "Türkiye: search regulator decisions",
    "tr_kurum_karari_getir": "Türkiye: get a regulator decision",
    "tr_kurum_listesi": "Türkiye: list supported regulators",
    "tr_spk_bulten_icinde_ara": "Türkiye: search within a Capital Markets Board bulletin",
    "tr_semantik_ara": "Türkiye: hybrid search of the local archive",
    "tr_belge_getir": "Türkiye: get a local archive document",
    "tr_hukuk_arastirma_rehberi": "Türkiye: research guide (which tool for which question)",
    # Netherlands
    "nl_search_caselaw": "Netherlands: search case law",
    "nl_browse_caselaw": "Netherlands: browse case law by filters",
    "nl_get_decision": "Netherlands: get a decision by ECLI",
    "nl_search_legislation": "Netherlands: search legislation",
    "nl_list_vocabulary": "Netherlands: list case-law filter values",
    # Poland
    "pl_search_by_title": "Poland: search acts by title",
    "pl_search_indexed": "Poland: subject search of acts",
    "pl_get_act": "Poland: get act metadata",
    "pl_get_act_text": "Poland: get act text",
    "pl_list_year": "Poland: list acts published in a year",
    # Austria
    "at_search_legislation": "Austria: search legislation",
    "at_search_caselaw": "Austria: search case law",
    "at_fetch_document": "Austria: get a RIS document",
    # Ireland
    "ie_search_acts": "Ireland: search Acts",
    "ie_get_section": "Ireland: get a section of an Act",
    "ie_get_act": "Ireland: get a whole Act",
    "ie_list_year": "Ireland: list Acts of a year",
    # Finland
    "fi_search_acts": "Finland: search legislation",
    "fi_browse_year": "Finland: list acts of a year",
    "fi_get_act": "Finland: get an act",
    "fi_recent_changes": "Finland: recently changed acts",
    # Spain
    "es_search_legislation": "Spain: search legislation",
    "es_get_act": "Spain: get act metadata",
    "es_get_act_text": "Spain: get consolidated act text",
    "es_get_document": "Spain: get a BOE document as published",
    "es_get_daily_gazette": "Spain: BOE contents of one day",
    # United Kingdom
    "uk_search_legislation": "UK: search legislation",
    "uk_get_legislation": "UK: get legislation metadata",
    "uk_get_section": "UK: get a section",
    "uk_get_contents": "UK: legislation table of contents",
    "uk_get_effects": "UK: amendments and repeals (effects)",
    # European Union
    "eu_search_eu_law": "EU: search EU law by title",
    "eu_get_metadata": "EU: get CELEX metadata",
    "eu_get_document_text": "EU: get document text",
    "eu_sparql": "EU: run a CELLAR SPARQL query",
    # Japan
    "jp_search_laws": "Japan: search statutes",
    "jp_list_laws": "Japan: list statutes",
    "jp_get_law": "Japan: get statute metadata",
    "jp_get_law_text": "Japan: get statute text",
    "jp_get_article": "Japan: get an article",
    # Legal-entity identity
    "gleif_search_entities": "LEI: search legal entities",
    "gleif_autocomplete": "LEI: resolve a company name",
    "gleif_get_entity": "LEI: get an entity record",
    "gleif_get_group_structure": "LEI: get parents and children",
    # Land registry (main endpoint only; not in the directory profile)
    "tkgm_baslangic": "Land registry: guide and limits",
    "tkgm_parsel_sorgula": "Land registry: live parcel lookup",
    "tkgm_konumdan_parsel": "Land registry: parcel at a coordinate",
    "tkgm_yer_bul": "Land registry: find a place",
    "tkgm_parsel_raporu": "Land registry: parcel report",
    "tkgm_baglanti": "Land registry: Parsel Sorgu links",
    "tkgm_parsel_oku": "Land registry: read a parcel file",
    "tkgm_geometri": "Land registry: parcel measurements",
    "tkgm_kroki": "Land registry: scaled sketch",
    "tkgm_disa_aktar": "Land registry: convert a parcel to another format",
    "tkgm_koordinat_donustur": "Land registry: convert coordinates",
    "tkgm_dayanak_koprusu": "Land registry: legal basis for a parcel question",
    "tkgm_koridor_kesisim": "Land registry: route corridor intersection",
    "tkgm_harita": "Land registry: interactive map",
    "tkgm_tarife_kalemi": "Land registry: fee tariff lookup",
    "tkgm_harc_hesapla": "Land registry: fee estimate",
    "tkgm_arayuze_yaz": "Land registry: write an answer to the local UI",
    # local (stdio) mode only
    "tkgm_arazi_3d": "Land registry: terrain and slope with a 3D view",
    "tkgm_parsel_foyu": "Land registry: Word sheet for one parcel",
    "tkgm_portfoy_tablosu": "Land registry: Excel table for many parcels",
    "tkgm_tapu_kaydi_oku": "Land registry: structure and mask a title record",
    # Azerbaijan
    "az_search_acts": "Azerbaijan: search legal acts",
    "az_count_acts": "Azerbaijan: count matching acts",
    "az_get_act": "Azerbaijan: get act metadata",
    "az_get_act_fulltext": "Azerbaijan: get act full text",
    "az_list_types": "Azerbaijan: list act types",
    "az_list_sections": "Azerbaijan: list act sections",
    # Legal scholarship
    "scholar_search_legal_scholarship": "Scholarship: search open-access legal articles",
    "scholar_compare_jurisdictions": "Scholarship: compare jurisdictions",
    "scholar_get_scholarship_article": "Scholarship: get article metadata",
    "scholar_get_scholarship_fulltext": "Scholarship: get article full text",
    "scholar_resolve_doi": "Scholarship: resolve a DOI",
    "scholar_list_sources": "Scholarship: list sources",
    # Published resource contracts
    "contracts_search_contracts": "Contracts: search petroleum and mining contracts",
    "contracts_count_contracts": "Contracts: count matching contracts",
    "contracts_get_contract_metadata": "Contracts: get contract metadata",
    "contracts_get_contract_text": "Contracts: get contract text",
    "contracts_get_contract_annotations": "Contracts: get key-clause annotations",
    "contracts_list_countries": "Contracts: list countries",
    "contracts_list_resources": "Contracts: list resources",
    "contracts_list_years": "Contracts: list signature years",
    "contracts_list_annotation_categories": "Contracts: list annotation categories",
    # Germany
    "de_search": "Germany: search federal legislation",
    "de_get_act": "Germany: get act metadata",
    "de_get_text": "Germany: get act text",
    "de_list_publishers": "Germany: list publication organs",
    "de_recent_changes": "Germany: recently published acts",
    "de_case_search": "Germany: search federal court decisions (NeuRIS)",
    "de_get_decision": "Germany: get decision metadata",
    "de_get_decision_text": "Germany: get decision text",
    "de_rii_case_search": "Germany: search federal court decisions (RII)",
    "de_rii_get_case_text": "Germany: get a decision text (RII)",
    "de_oldp_case_search": "Germany: search case law at all court levels (Open Legal Data)",
    "de_oldp_get_case": "Germany: get a decision (Open Legal Data)",
    "de_dip_search": "Germany: search Bundestag documents (DIP)",
    "de_coverage": "Germany: coverage and sources",
    "de_dip_get_document": "Germany: get a Bundestag document",
    "de_norm_getir": "Germany: get a statutory provision (gesetze-im-internet.de)",
    "de_gesetz_ara": "Germany: find a statute by name (gesetze-im-internet.de)",
    # Whole endpoint
    "status": "Status of every jurisdiction",
    "al_masasi": "ArthurLegal desk (interactive view)",
}

# Tools that can change something (locally: write a file or the UI answer).
# Everything else is read-only.
NOT_READ_ONLY = {
    "tkgm_arayuze_yaz", "tkgm_kroki", "tkgm_harita", "tkgm_disa_aktar",
    "tkgm_arazi_3d", "tkgm_parsel_foyu", "tkgm_portfoy_tablosu",
}


def title_for(name: str) -> str:
    """The table entry, or a readable fallback built from the tool name."""
    if name in TITLES:
        return TITLES[name]
    prefix, _, rest = name.partition("_")
    label = PREFIX_LABELS.get(prefix)
    if label and rest:
        return "%s: %s" % (label, rest.replace("_", " "))
    return name.replace("_", " ")


def annotations_for(name: str) -> Dict[str, Any]:
    title = title_for(name)
    if name in NOT_READ_ONLY:
        return {"title": title, "readOnlyHint": False, "destructiveHint": False}
    return {"title": title, "readOnlyHint": True}


def annotate(tool: Any) -> None:
    """Set ``title`` and ``annotations`` on an mcpcore.Tool in place."""
    tool.title = title_for(tool.name)
    tool.annotations = annotations_for(tool.name)
