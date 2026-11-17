"""Tests for candidate selection - the part that can quietly poison the list.

Storing the wrong director's name is worse than storing none, so most of
these assert that nothing is picked rather than that something is.
"""

from workers.egrul.dadata_client import PartyCandidate
from workers.egrul.matcher import name_similarity, normalize_name, pick_candidate


def candidate(name, ceo="Иванов Иван Иванович", post="Генеральный директор",
              inn="7700000001", city="Москва", status="ACTIVE"):
    return PartyCandidate(name=name, legal_name=name, inn=inn, ogrn="1"+inn,
                          ceo_name=ceo, ceo_post=post, status=status, city=city)


class TestNormalization:
    def test_legal_form_is_not_part_of_the_name(self):
        assert normalize_name('ООО "Ромашка"') == normalize_name("Ромашка")

    def test_yo_is_folded(self):
        assert normalize_name("Тёплый дом") == normalize_name("Теплый дом")

    def test_full_legal_form_spelled_out_is_stripped_too(self):
        assert normalize_name("Общество с ограниченной ответственностью Ромашка") == "ромашка"

    def test_identical_names_score_one(self):
        assert name_similarity('ООО "Ромашка"', "Ромашка") == 1.0

    def test_unrelated_names_score_low(self):
        assert name_similarity("Ромашка", "Газпром") < 0.5


class TestPicking:
    def test_exact_name_match_is_taken(self):
        picked, note = pick_candidate("Ромашка", "Москва", [candidate('ООО "Ромашка"')])

        assert picked is not None
        assert picked.ceo_name == "Иванов Иван Иванович"
        assert "совпадение" in note

    def test_weak_match_is_rejected(self):
        picked, note = pick_candidate("Ромашка", "Москва", [candidate('ООО "Гвоздика"')])

        assert picked is None
        assert "ниже порога" in note

    def test_two_equally_similar_companies_are_refused(self):
        # Two live companies with the same name in the same city. Either
        # choice is a coin flip, and a coin flip is not data.
        picked, note = pick_candidate("Ромашка", "Москва", [
            candidate('ООО "Ромашка"', ceo="Иванов И.И.", inn="7700000001"),
            candidate('ООО "Ромашка"', ceo="Петров П.П.", inn="7700000002"),
        ])

        assert picked is None
        assert "неоднозначен" in note

    def test_same_city_breaks_a_tie_in_favour_of_the_local_one(self):
        picked, _ = pick_candidate("Ромашка Плюс", "Казань", [
            candidate("Ромашка Плюсс", ceo="Дальний", inn="7700000001", city="Москва"),
            candidate("Ромашка Плюс", ceo="Местный", inn="1600000002", city="Казань"),
        ])

        assert picked is not None
        assert picked.ceo_name == "Местный"

    def test_liquidated_company_is_skipped(self):
        picked, note = pick_candidate("Ромашка", "Москва", [
            candidate('ООО "Ромашка"', status="LIQUIDATED"),
        ])

        assert picked is None
        assert "ликвидированы" in note

    def test_liquidated_is_kept_when_active_only_is_off(self):
        picked, _ = pick_candidate("Ромашка", "Москва",
                                   [candidate('ООО "Ромашка"', status="LIQUIDATED")],
                                   active_only=False)

        assert picked is not None

    def test_candidate_without_a_ceo_is_useless(self):
        picked, note = pick_candidate("Ромашка", "Москва", [candidate('ООО "Ромашка"', ceo=None)])

        assert picked is None
        assert "без руководителя" in note

    def test_empty_candidate_list(self):
        picked, note = pick_candidate("Ромашка", "Москва", [])

        assert picked is None
        assert note

    def test_missing_city_does_not_disqualify(self):
        # OSM city data is patchy; a missing city must not cost a good match.
        picked, _ = pick_candidate("Ромашка", None, [candidate('ООО "Ромашка"', city=None)])

        assert picked is not None

    def test_unknown_status_is_treated_as_active(self):
        picked, _ = pick_candidate("Ромашка", "Москва", [candidate('ООО "Ромашка"', status=None)])

        assert picked is not None
