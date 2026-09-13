"""Temporary safety net for the state.py modularization refactor.

Snapshot of every name importable from ed_companion.phase14.state, taken
BEFORE the refactor began. As functions move into new state_*.py files,
state.py re-exports them explicitly - this test guarantees no name is
silently lost or renamed in that process. Delete this file once the
refactor (see the plan in C:\\Users\\Gerri\\.claude\\plans) is complete and
merged; it exists only to de-risk the migration itself, not as permanent
coverage.
"""

import unittest

import ed_companion.phase14.state as state

NAMES_BEFORE_REFACTOR = frozenset({
    "Any", "BLUEPRINT_ID_CATALOG_PATH", "COMMANDER_RANK_CATEGORIES",
    "COMMANDER_REPUTATIONS", "ENGINEERING_CATEGORY_ORDER",
    "ENGINEERING_MODULE_CATEGORIES", "ENGINEERING_MODULE_ID_PREFIXES",
    "ENGINEER_NAME_ALIASES", "EXPERIMENTAL_STATUS_LABELS",
    "GRADE_STATUS_LABELS", "HEURISTIC_TRADER_WARNING_KEY",
    "JOURNAL_BLUEPRINT_NAMES", "JOURNAL_EXPERIMENTAL_NAMES",
    "LOADOUT_PROJECTION_EVENTS", "LOGBOOK_CATEGORIES", "LOGBOOK_FILTERS",
    "LOGBOOK_LIMIT", "LOGBOOK_NOTE_LIMIT", "LOGGER",
    "MANDATORY_CORE_STOCK_FAMILIES", "MATERIAL_CATEGORIES",
    "MATERIAL_STATUS", "PROGRESS_STATUS", "Path", "ProfileContext",
    "RAW_GROUP_FARMS", "SESSION_HISTORY_LIMIT", "TraderTypeCache",
    "_CRAFT_BATCH_LOCK", "_JOURNAL_EVENT_CACHE", "_JOURNAL_EVENT_CACHE_LOCK",
    "_JOURNAL_GLOB_CACHE", "_JOURNAL_GLOB_CACHE_LOCK",
    "_JOURNAL_GLOB_TTL_SECONDS", "_JOURNAL_POLL_FILE_LIMIT",
    "_UNLOCK_EVENT_CACHE", "_cached_profile_loadout_slots_by_ship",
    "_craft_can_bind", "_craft_events_with_ship_context",
    "_craft_matches_binding", "_craft_matches_unique_equivalent_slot",
    "_dismiss_craft_tracking_issues_locked",
    "_eligible_plan_ids_after_baseline", "_engineer_leg_distance",
    "_experimental_craft_matches", "_fast_journal_profile_identity",
    "_grade_craft_matches_blueprint", "_ingredient_display_signature",
    "_ingredient_signature", "_journal_guard", "_journal_profile_identity",
    "_journal_snapshot", "_logbook_entry", "_logbook_material_is_interesting",
    "_merge_capi_fleet_roster", "_minimum_engineer_cover",
    "_module_display_catalog", "_newer_station_location",
    "_recent_journal_names", "_reconcile_engineer_craft_batch_locked",
    "_shortest_engineer_route", "_singular_effect_key", "_source_distance",
    "_supplement_capi_ranks", "_supplement_capi_ship_value",
    "_write_json_if_changed", "actionable_source_card",
    "active_profile_identity", "active_profile_key",
    "aggregate_plan_progress", "annotate_installed_target_conflicts",
    "app_data_dir", "apply_engineer_craft", "apply_session_event",
    "assign_plans_to_nearest_engineers", "atomic_write",
    "attach_operation_experimental_effects", "attach_operation_plan_context",
    "augmented_species_catalog", "best_find", "blueprint_catalog",
    "blueprint_id_evidence", "blueprint_module_family", "blueprint_rows",
    "build_engineering_plan", "build_experimental_plan", "build_state",
    "build_trader_route", "canonical_cargo_materials",
    "canonical_module_id", "capi_loadout_slots",
    "classify_craft_tracking_issues", "clear_journal_event_cache",
    "commander_journal_overview", "commander_status_credits",
    "craft_issue_matches_plan", "craft_issue_row",
    "craft_tracking_issues_for_ship", "current_cargo_event", "current_ship",
    "dataclass", "datetime", "deepcopy", "defaultdict",
    "discard_bound_module_plans", "dismiss_craft_tracking_issue",
    "dismiss_historical_craft_tracking_issues",
    "dismiss_selected_craft_tracking_issues", "duplicate_ship_plan",
    "engineer_craft_fingerprint", "engineer_options_for_plan",
    "engineer_progress_from_events", "engineer_unlock_signals",
    "engineering_loadout_rows", "engineering_module_category",
    "engineering_run_preflight", "exobiology_carried_summary",
    "exobiology_findings", "exobiology_lifetime_earned",
    "exobiology_session_summary", "exobiology_summary",
    "extract_local_hge_sightings", "extract_local_state_finds",
    "filter_logbook_entries", "find_nearest_catalog_trader",
    "genus_completion", "hashlib", "inventory_from_events",
    "is_completed_engineer_craft", "is_craft_before_safe_plan_baseline",
    "is_hge_material", "is_material_tradeable",
    "is_unconfirmed_legacy_history", "journal_change_signature",
    "journal_change_summary", "journal_craft_baseline", "journal_dir",
    "journal_events", "journal_material_name", "journal_paths_for_profile",
    "journal_unlock_events", "json", "landing_targets",
    "latest_loadout_slots", "latest_loadout_slots_by_ship",
    "latest_profile_location", "learn_blueprint_id_catalog",
    "load_blueprint_id_catalog", "load_json_file", "load_logbook_notes",
    "load_session_history", "load_unlock_catalog",
    "load_user_trader_catalog", "local_hge_scan_status",
    "local_state_find_scan_status", "logbook_entries", "logging",
    "lru_cache", "material_completion", "material_event_changes",
    "material_key", "material_metadata", "material_status_label",
    "material_trade_options", "math", "merge_capi_commander_overview",
    "merge_capi_fleet", "merge_capi_loadout", "merge_trader_catalog",
    "migrate_legacy_plan_baselines", "migrate_wishlist_bindings",
    "module_identity_key", "module_matches_type",
    "module_purchase_identity", "module_store_core_replacement",
    "move_ship_plan", "normalize", "normalize_session_history",
    "normalize_timestamp", "operation_physical_slot_label",
    "operation_plan_identity", "os", "partition_engineer_assignments",
    "persistence_issues", "plan_material_trades", "planner_mode",
    "planner_physical_identity", "powerplay_journal_overview",
    "profiled_journal_events", "progress_status_label",
    "project_latest_srv_mining_session", "project_local_mining_evidence",
    "project_vehicle_state", "public_session", "re", "read_journal_tail",
    "read_journal_tail_records", "read_json", "real_engineers",
    "rebuild_fleet", "reconcile_engineer_craft_batch",
    "reconcile_fleet_cache", "reference_data_dir", "remaining_grade_rolls",
    "remaining_signals_at_body", "remove_ship_task", "replace_ship_plan",
    "required_materials", "reserve_material_pool", "resolve_profile_context",
    "resolve_trader_type", "runtime_data_dir", "same_module_identity",
    "scope_operation_action_materials", "select_operation_action",
    "session_statistics", "set_journal_dir", "set_prioritized_ship_plan",
    "set_tech_broker_track", "ship_journal_events", "ship_slot_layout",
    "source_cards", "spansh_trader_type_evidence", "task_signature",
    "technology_broker_unlock_guide", "threading", "time", "timezone",
    "trade_batch", "trade_matches_trader", "trader_type_evidence_from_event",
    "update_trader_type_evidence", "user_trader_catalog_path", "uuid",
    "wishlist_target_status", "write_logbook_note", "write_ship_tasks",
})


class StateModuleNameRegressionTests(unittest.TestCase):
    def test_every_pre_refactor_name_is_still_importable_from_state(self):
        current = {n for n in dir(state) if not n.startswith("__")}
        missing = NAMES_BEFORE_REFACTOR - current
        self.assertFalse(
            missing,
            f"Names lost from ed_companion.phase14.state during the "
            f"modularization refactor: {sorted(missing)}",
        )


if __name__ == "__main__":
    unittest.main()
