"""Tests for station merging logic (duplicate Paddington, Shepherd's Bush, etc.)."""

from datetime import date

import pytest

from src.config import load_config
from src.gtfs.parser import GTFSParser


LONDON_CONFIG = "configs/london.yaml"
TARGET_DATE = date(2026, 3, 24)


@pytest.fixture(scope="module")
def london_parsed():
    config = load_config(LONDON_CONFIG)
    parser = GTFSParser(config)
    return parser.parse(target_date=TARGET_DATE)


class TestStationMerging:
    def test_272_station_count(self, london_parsed):
        """After merging duplicates, we should have exactly 272 required stations."""
        assert len(london_parsed.required_station_ids) == 272

    def test_paddington_single_station(self, london_parsed):
        """Paddington H&C (940GZZLUPAH) should be merged into Paddington (940GZZLUPAC)."""
        paddington_ids = [
            sid for sid, s in london_parsed.stations.items()
            if "paddington" in s.name.lower()
        ]
        assert len(paddington_ids) == 1
        assert paddington_ids[0] == "940GZZLUPAC"

    def test_paddington_has_all_children(self, london_parsed):
        """Merged Paddington should have children from both original parent stations."""
        paddington = london_parsed.stations["940GZZLUPAC"]
        # Should have children from both 940GZZLUPAC (4 platforms) and 940GZZLUPAH (2 platforms)
        assert len(paddington.child_stop_ids) >= 6
        assert "9400ZZLUPAH1" in paddington.child_stop_ids
        assert "9400ZZLUPAH2" in paddington.child_stop_ids
        assert "9400ZZLUPAC1" in paddington.child_stop_ids

    def test_shepherds_bush_central_single(self, london_parsed):
        """Shepherd's Bush Central orphan should be merged into the parent."""
        sbc_ids = [
            sid for sid, s in london_parsed.stations.items()
            if "shepherd" in s.name.lower() and "bush" in s.name.lower()
            and "market" not in s.name.lower()
        ]
        assert len(sbc_ids) == 1
        assert sbc_ids[0] == "940GZZLUSBC"

    def test_shepherds_bush_market_separate(self, london_parsed):
        """Shepherd's Bush Market should remain a separate station."""
        sbm_ids = [
            sid for sid, s in london_parsed.stations.items()
            if "shepherd" in s.name.lower() and "market" in s.name.lower()
        ]
        assert len(sbm_ids) == 1

    def test_edgware_road_stays_separate(self, london_parsed):
        """Edgware Road has two genuinely separate stations — both must remain."""
        er_ids = [
            sid for sid, s in london_parsed.stations.items()
            if "edgware road" in s.name.lower()
        ]
        assert len(er_ids) == 2
        er_station_ids = set(er_ids)
        assert "940GZZLUERB" in er_station_ids  # Bakerloo
        assert "940GZZLUERC" in er_station_ids  # Circle

    def test_hammersmith_stays_separate(self, london_parsed):
        """Hammersmith has two genuinely separate stations — both must remain."""
        ham_ids = [
            sid for sid, s in london_parsed.stations.items()
            if "hammersmith" in s.name.lower()
        ]
        assert len(ham_ids) == 2
        ham_station_ids = set(ham_ids)
        assert "940GZZLUHSD" in ham_station_ids  # Dist & Picc
        assert "940GZZLUHSC" in ham_station_ids  # H&C

    def test_kensington_olympia_present(self, london_parsed):
        """Kensington Olympia should still be present (not excluded in 272 ruleset)."""
        assert "940GZZLUKOY" in london_parsed.stations
        assert "940GZZLUKOY" in london_parsed.required_station_ids
