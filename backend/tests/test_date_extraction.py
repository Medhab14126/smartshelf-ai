from datetime import date

import pytest

from app.services.date_extraction_service import FieldStatus, ParseStatus, extract_dates, parse_date_string

TODAY = date(2026, 10, 29)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("29/10/2026", date(2026, 10, 29)),
        ("29-10-2026", date(2026, 10, 29)),
        ("29.10.2026", date(2026, 10, 29)),
        ("2026-10-29", date(2026, 10, 29)),
        ("29 Oct 2026", date(2026, 10, 29)),
        ("29-OCT-26", date(2026, 10, 29)),
        ("October 29, 2026", date(2026, 10, 29)),
        ("29th October 2026", date(2026, 10, 29)),
    ],
)
def test_parses_common_formats(text, expected):
    parsed = parse_date_string(text)
    assert parsed is not None
    assert parsed.status == ParseStatus.VALID
    assert parsed.value == expected


def test_detects_day_month_ambiguity_and_respects_preference():
    dmy = parse_date_string("03/04/2026", "DMY")
    mdy = parse_date_string("03/04/2026", "MDY")
    assert dmy.status == mdy.status == ParseStatus.AMBIGUOUS
    assert dmy.value == date(2026, 4, 3) and dmy.alternatives == [date(2026, 3, 4)]
    assert mdy.value == date(2026, 3, 4)


def test_same_day_and_month_is_not_ambiguous():
    assert parse_date_string("05/05/2026").status == ParseStatus.VALID


def test_invalid_calendar_date():
    assert parse_date_string("31/02/2027").status == ParseStatus.INVALID
    result = extract_dates("EXP: 31/02/2027", today=TODAY)
    assert result.fields["expiry_date"].status == FieldStatus.UNCERTAIN
    assert result.fields["expiry_date"].value is None


def test_labels_distinguish_manufacturing_packing_expiry_and_best_before():
    text = "MFG: 15/10/2026\nPKD ON 16/10/2026\nUSE BY 20/11/2026\nBEST BEFORE 30/11/2026"
    fields = extract_dates(text, today=TODAY).fields
    assert fields["manufacturing_date"].value == date(2026, 10, 15)
    assert fields["packing_date"].value == date(2026, 10, 16)
    assert fields["expiry_date"].value == date(2026, 11, 20)
    assert fields["best_before_date"].value == date(2026, 11, 30)
    assert fields["manufacturing_date"].status == FieldStatus.DETECTED


def test_same_line_labels_pair_with_their_own_dates():
    fields = extract_dates("MFG: 20.10.2026 EXP: 20.12.2026", today=TODAY).fields
    assert fields["manufacturing_date"].value == date(2026, 10, 20)
    assert fields["expiry_date"].value == date(2026, 12, 20)


def test_unlabelled_dates_are_never_assumed_to_be_expiry():
    result = extract_dates("Printed 2026-10-20\n2027-05-12", today=TODAY)
    assert result.fields["expiry_date"].status == FieldStatus.NOT_FOUND
    assert result.fields["expiry_date"].value is None
    assert len([c for c in result.candidates if c.field == "unknown"]) == 2
    assert any("without a recognisable label" in w for w in result.warnings)


def test_labels_on_previous_line_are_uncertain():
    fields = extract_dates("MFG DATE   EXP DATE\n15/10/2026  15/04/2027", today=TODAY).fields
    assert fields["manufacturing_date"].value == date(2026, 10, 15)
    assert fields["expiry_date"].value == date(2027, 4, 15)
    assert fields["expiry_date"].status == FieldStatus.UNCERTAIN


def test_ambiguous_expiry_is_flagged_with_both_options():
    field = extract_dates("PKD 03/04/2027\nUSE BY 05/04/2027", today=TODAY).fields["expiry_date"]
    assert field.status == FieldStatus.AMBIGUOUS
    assert set(field.options) == {date(2027, 4, 5), date(2027, 5, 4)}


def test_order_inferred_from_unambiguous_sibling_date_is_still_uncertain():
    field = extract_dates("MFG: 25/10/2026 EXP: 01/11/2026", today=TODAY).fields["expiry_date"]
    assert field.value == date(2026, 11, 1)
    assert field.status == FieldStatus.UNCERTAIN


def test_month_year_only_is_partial_and_uncertain():
    field = extract_dates("EXP 11/2027", today=TODAY).fields["expiry_date"]
    assert field.value == date(2027, 11, 30)
    assert field.status == FieldStatus.UNCERTAIN
    assert any("month and year" in r for r in field.reasons)


def test_relative_best_before_is_derived_and_uncertain():
    fields = extract_dates("Best before 6 months from manufacture\nMFG 15/09/2026", today=TODAY).fields
    assert fields["best_before_date"].value == date(2027, 3, 15)
    assert fields["best_before_date"].status == FieldStatus.UNCERTAIN


def test_expiry_before_manufacturing_is_flagged():
    result = extract_dates("MFG: 20/12/2026 EXP: 15/10/2026", today=TODAY)
    assert result.fields["expiry_date"].status == FieldStatus.UNCERTAIN
    assert any("not after the manufacturing date" in w for w in result.warnings)


def test_ocr_letter_digit_confusion_is_corrected_but_marked():
    field = extract_dates("EXP: 2O/12/2O26", today=TODAY).fields["expiry_date"]
    assert field.value == date(2026, 12, 20)
    assert field.status == FieldStatus.UNCERTAIN


def test_export_is_not_an_expiry_label():
    assert extract_dates("EXPORT QUALITY 12/12/2026", today=TODAY).fields["expiry_date"].status == FieldStatus.NOT_FOUND


def test_conflicting_expiry_values_are_ambiguous():
    field = extract_dates("EXP 20/12/2026\nEXPIRY 22/12/2026", today=TODAY).fields["expiry_date"]
    assert field.status == FieldStatus.AMBIGUOUS
