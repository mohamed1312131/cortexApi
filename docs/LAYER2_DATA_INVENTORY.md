# Cortex Layer 2 Data Inventory

## Summary

This document inventories the JSON assets under `data/` that support Layer 2.
Layer 2 uses these files as local planning references for road, sea, and air
fact building.

Important limits:

- These files are not live logistics systems.
- They do not confirm booking, space, rates, schedules, permits, customs
  clearance, legal compliance, or carrier acceptance.
- Connectors should surface selected evidence from these files, not dump raw
  datasets into `BlockResponse.data`.
- Missing records or missing fields become unknowns, not all-clear results.

## Role Labels

| Role | Meaning |
| --- | --- |
| `main` | Primary dataset used by a connector. |
| `rules` | Rule/reference file used for matching, gates, readiness, or validation. |
| `confidence` | Confidence cap or confidence policy rules. |
| `metadata` | Dataset metadata, limitations, coverage, source notes, or integration notes. |
| `support` | Mapping or support reference. |
| `coverage` | Coverage or quality summary. |
| `source_refs` | Source inventory or source reference list. |
| `auxiliary` | Useful data asset that is not classified as the main connector dataset. |

## Air Data Files

| File | Block | Role | Contains |
| --- | --- | --- | --- |
| `data/air/air_airports.json` | `AIR-C` | support | 4,564 airport records with IATA/ICAO code, name, city, country, coordinates, airport type, scheduled service flag, cargo tier, 2024 cargo tonnage, and tonnage source. |
| `data/air/air_carriers.json` | `AIR-D` | support | Air carrier reference with 14 cargo carrier records plus metadata, aircraft cargo specs, and lane inference rules. |
| `data/air/air_reference.json` | `AIR-COST` | support/main for cost | Air planning reference containing air documents, chargeable weight settings, USD/kg rate buckets, surcharge percentages, transit-day buckets, ULD specs, and dangerous goods air rules. |
| `data/air/cortex_air_block_a_dg_records_REPAIRED.json` | `AIR-A` | main | 2,715 air dangerous-goods records with record id, symbols, proper shipping name, hazard class/division, UN number, packing group, label codes, special provisions, packing instructions, passenger/cargo aircraft quantity limits, air acceptance status, CAO requirement, and hard gate flag. |
| `data/air/cortex_air_block_b_category_rules.json` | `AIR-B` | rules | 10 special-handling category rules with category id/name, handling codes, required intake fields, hard gates, planning factors, confidence cap, and source docs. |
| `data/air/cortex_air_block_b_confidence_rules.json` | `AIR-B` | confidence | 10 confidence rules for special-handling conditions with cap percentages and messages. |
| `data/air/cortex_air_block_b_dataset.json` | `AIR-B` | main | Combined AIR-B dataset containing 90 special handling codes, 10 category rules, 10 confidence rules, 9 source refs, and metadata. |
| `data/air/cortex_air_block_b_metadata.json` | `AIR-B` | metadata | AIR-B metadata with dataset name, version, purpose, legal note, coverage summary, integration note, and 10 main categories. |
| `data/air/cortex_air_block_b_source_refs.json` | `AIR-B` | source_refs | 9 source references for special handling code/category data. |
| `data/air/cortex_air_block_b_special_handling_codes.json` | `AIR-B` | auxiliary | 90 standalone special handling code records with code, description, category family, primary category, DG relevance, Block B relevance, risk level, and source note. |
| `data/air/cortex_air_block_c_airport_capabilities.json` | `AIR-C` | auxiliary | 39 standalone airport capability records with cargo terminal, customs, DG handling, pharma/cold-chain, temperature storage, frozen storage, perishable/live animal/value/oversized handling, ULD handling, freighter/main deck handling, security screening, handlers, and operating notes. |
| `data/air/cortex_air_block_c_confidence_rules.json` | `AIR-C` | confidence | 6 confidence rules for airport capability completeness and field requirements. |
| `data/air/cortex_air_block_c_dataset.json` | `AIR-C` | main | Combined AIR-C dataset containing 39 airport capability records, 6 confidence rules, and metadata. |
| `data/air/cortex_air_block_c_metadata.json` | `AIR-C` | metadata | AIR-C metadata with dataset name, version, purpose, legal note, coverage, and 5 recommended next research items. |
| `data/air/cortex_air_block_d_carrier_capabilities.json` | `AIR-D` | auxiliary | 10 standalone carrier capability records with carrier code/name/type, home hub, DG, dry ice, pharma, temperature, perishables, live animals, valuable cargo, human remains, oversized, freighter network, and restriction fields. |
| `data/air/cortex_air_block_d_confidence_rules.json` | `AIR-D` | confidence | 9 confidence rules for carrier capability data requirements. |
| `data/air/cortex_air_block_d_dataset.json` | `AIR-D` | main | Combined AIR-D dataset containing 10 carrier capability records, 9 confidence rules, and metadata. |
| `data/air/cortex_air_block_d_metadata.json` | `AIR-D` | metadata | AIR-D metadata with purpose, legal note, coverage, conservative policy, and 5 recommended next research items. |
| `data/air/cortex_air_block_e_aircraft_fit_specs.json` | `AIR-E` | auxiliary | 7 standalone aircraft fit specs with aircraft type/role, main deck/lower deck/nose door availability, door dimensions, and cargo fit fields. |
| `data/air/cortex_air_block_e_dataset.json` | `AIR-E` | main | Combined AIR-E dataset containing 7 aircraft fit specs, 10 ULD specs, 9 fit rules, 8 source refs, and metadata. |
| `data/air/cortex_air_block_e_fit_rules.json` | `AIR-E` | rules | 9 aircraft/ULD fit rules with rule id/name, applicable codes, condition, action, confidence cap, and message. |
| `data/air/cortex_air_block_e_metadata.json` | `AIR-E` | metadata | AIR-E metadata with purpose, legal note, coverage, integration note, and 5 recommended next research items. |
| `data/air/cortex_air_block_e_source_refs.json` | `AIR-E` | source_refs | 8 source references for aircraft and ULD fit data. |
| `data/air/cortex_air_block_e_uld_specs.json` | `AIR-E` | auxiliary | 10 standalone ULD specs with ULD code/name/family, deck type, external dimensions, usable dimensions, and fit fields. |
| `data/air/cortex_air_block_f_airport_jurisdiction_map.json` | `AIR-F` | support | 9 airport-to-jurisdiction mapping records used to map country/airport context to Block F jurisdictions. |
| `data/air/cortex_air_block_f_border_rules.json` | `AIR-F` | rules | 20 air border, permit, customs, and jurisdiction rules with jurisdiction, cargo category, applicable codes, permit/notification/certificate/inspection requirements, authorities, documents, and gate-if-missing fields. |
| `data/air/cortex_air_block_f_confidence_rules.json` | `AIR-F` | confidence | 7 confidence rules for air border/permit data. |
| `data/air/cortex_air_block_f_dataset.json` | `AIR-F` | main | Combined AIR-F dataset containing 9 airport jurisdiction map records, 20 border rules, 7 confidence rules, 15 source refs, and metadata. |
| `data/air/cortex_air_block_f_metadata.json` | `AIR-F` | metadata | AIR-F metadata with purpose, legal note, coverage, aligned airports, strong/medium/weak jurisdictions, and integration notes. |
| `data/air/cortex_air_block_f_source_refs.json` | `AIR-F` | source_refs | 15 source references for border, permit, and jurisdiction data. |
| `data/air/cortex_air_block_h_confidence_rules.json` | `AIR-H` | confidence | 8 confidence rules for air security/screening/PLACI readiness. |
| `data/air/cortex_air_block_h_dataset.json` | `AIR-H` | main | Combined AIR-H dataset containing 9 security status matrix rows, 12 jurisdiction security rules, 10 PLACI minimum data records, 8 confidence rules, 6 source refs, and metadata. |
| `data/air/cortex_air_block_h_jurisdiction_security_rules.json` | `AIR-H` | rules | 12 jurisdiction-specific security rules with jurisdiction, applicability, condition, required security action, hard gate if missing, confidence cap, and source details. |
| `data/air/cortex_air_block_h_metadata.json` | `AIR-H` | metadata | AIR-H metadata with purpose, legal note, coverage, integration note, 9 important security codes, and 4 recommended next research items. |
| `data/air/cortex_air_block_h_placi_minimum_data.json` | `AIR-H` | auxiliary | 10 PLACI minimum data fields with field name, required-for scope, missing-data impact, and notes. |
| `data/air/cortex_air_block_h_security_status_matrix.json` | `AIR-H` | rules | 9 security status code records with passenger/cargo eligibility, screening, secure supply chain, PLACI check, station confirmation, hard gate if unknown, confidence cap, and notes. |
| `data/air/cortex_air_block_h_source_refs.json` | `AIR-H` | source_refs | 6 source references for security and PLACI data. |
| `data/air/cortex_air_block_i_confidence_rules.json` | `AIR-I` | confidence | 8 confidence rules for air route and schedule readiness. |
| `data/air/cortex_air_block_i_dataset.json` | `AIR-I` | main | Combined AIR-I dataset containing 10 route feasibility rules, 18 schedule input requirements, 17 tracking milestones, 10 route risk rules, 8 confidence rules, 9 source refs, and metadata. |
| `data/air/cortex_air_block_i_metadata.json` | `AIR-I` | metadata | AIR-I metadata with purpose, legal note, coverage, integration note, and 5 recommended next research items. |
| `data/air/cortex_air_block_i_route_feasibility_rules.json` | `AIR-I` | rules | 10 route feasibility rules with route condition, cargo applicability, required data, risk if missing, confidence cap, and message. |
| `data/air/cortex_air_block_i_route_risk_rules.json` | `AIR-I` | rules | 10 route risk rules with risk code, condition, risk level, applicability, confidence impact, and recommended action. |
| `data/air/cortex_air_block_i_schedule_input_requirements.json` | `AIR-I` | auxiliary | 18 schedule input requirement records with schedule field, required-for context, why needed, missing impact, and examples. |
| `data/air/cortex_air_block_i_source_refs.json` | `AIR-I` | source_refs | 9 source references for route, schedule, tracking, and risk data. |
| `data/air/cortex_air_block_i_tracking_milestones.json` | `AIR-I` | auxiliary | 17 air tracking milestone records with milestone code/name, meaning, normal sequence order, exception flag, and Cortex interpretation. |

## Road Data Files

| File | Block | Role | Contains |
| --- | --- | --- | --- |
| `data/road/road_a_confidence_rules.json` | `ROAD-A` | confidence | 5 ADR/DG confidence rules with conditions, max confidence, source confidence, and cap-if-missing fields. |
| `data/road/road_a_dg_road_acceptance.json` | `ROAD-A` | main | 105 road dangerous-goods/ADR acceptance records with UN number, proper shipping name, hazard class, packing group, ADR tunnel code, tunnel meaning, limited quantity, road acceptance status, hard gate flag, factor type, source, and confidence. |
| `data/road/road_a_metadata.json` | `ROAD-A` | metadata | ROAD-A metadata with status, coverage summary, reasons the full ADR table is not included, and known limitations. |
| `data/road/road_a_schema.json` | `ROAD-A` | support | ROAD-A schema description with record type, 12 required fields, and field definitions. |
| `data/road/road_a_source_inventory.json` | `ROAD-A` | source_refs | ROAD-A source inventory with 4 sources, source type, use, confidence, and safety/copyright note. |
| `data/road/road_a_tunnel_code_meanings.json` | `ROAD-A` | auxiliary | 10 ADR tunnel code meaning records with code, meaning, forbidden tunnel categories, source, and confidence. |
| `data/road/road_a_validation_rules.json` | `ROAD-A` | rules | 6 validation rules for ROAD-A data with rule id, severity, condition, fix, and hard gate flag. |
| `data/road/road_b_abnormal_load_rules.json` | `ROAD-B` | auxiliary | 5 abnormal load rules with condition, permit requirement, hard gate flag, confidence cap, source, and confidence. |
| `data/road/road_b_confidence_rules.json` | `ROAD-B` | confidence | 5 ROAD-B confidence rules with missing-field confidence caps, max confidence, reasons, source, and confidence. |
| `data/road/road_b_metadata.json` | `ROAD-B` | metadata | ROAD-B metadata with purpose, route-optimizer limitation, coverage summary, known limitations, sources, and 8 files in bundle. |
| `data/road/road_b_standard_limits.json` | `ROAD-B` | auxiliary | 9 standard road limit records with limit id, parameter, value, unit, applicability, hard gate if exceeded, source, and confidence. |
| `data/road/road_b_vehicle_fit_profiles.json` | `ROAD-B` | main | 36 vehicle/load fit profile records with cargo profile id/name, required vehicle, standard-limit status, abnormal permit requirement, hard gate flag, standard limit basis, permit lead time note, operational note, confidence cap, source, and confidence. |
| `data/road/road_c_confidence_rules.json` | `ROAD-C` | confidence | 6 road corridor confidence rules with condition, corridor viability, cap-if-missing, max confidence, reason, source, and confidence. |
| `data/road/road_c_corridor_viability.json` | `ROAD-C` | main | 5,150 country-pair road corridor viability records with origin/destination country and name, corridor viability, permit type, cabotage flag, transit note, hard gate flag, factor type, confidence cap, and rule id. |
| `data/road/road_c_country_groups.json` | `ROAD-C` | support | 11 country group records with group id, country count, source, confidence, and country lists used for pair generation/context. |
| `data/road/road_c_metadata.json` | `ROAD-C` | metadata | ROAD-C metadata with purpose, route-optimizer limitation, design principle, coverage summary, known limitations, sources, and bundle files. |
| `data/road/road_c_pair_generation_rules.json` | `ROAD-C` | rules | 9 pair-generation rules with priority, corridor viability, applicability, hard gate flag, confidence cap, source, and confidence. |
| `data/road/road_cost_confidence_rules.json` | `ROAD-COST` | confidence | 6 road cost confidence rules with condition, confidence cap, max confidence, reason, source, and confidence. |
| `data/road/road_cost_metadata.json` | `ROAD-COST` | metadata | ROAD-COST metadata with purpose, not-a-full-block warning, coverage summary, known limitations, sources, and bundle files. |
| `data/road/road_cost_reference.json` | `ROAD-COST` | main | 19 road cost planning reference records with cost item id, category, region/scenario, unit, low/typical/high values, planning note, factor type, live/paid data need, source, and confidence. |
| `data/road/road_f_border_buffer_reference.json` | `ROAD-F` | auxiliary | 8 border buffer reference records with scenario, min/typical/max hours, factor type, source, and confidence. |
| `data/road/road_f_confidence_rules.json` | `ROAD-F` | confidence | 6 ROAD-F confidence rules with cap-if-missing, max confidence, reason, source, and confidence. |
| `data/road/road_f_document_requirements.json` | `ROAD-F` | main | 15 road document requirement records with document, when required, responsible party, hard gate if missing, factor type, confidence cap, source, and confidence. |
| `data/road/road_f_driver_hours_rules.json` | `ROAD-F` | auxiliary | 7 driver-hours rule records with rule id/name, value, unit, hard gate flag, factor type, source, and confidence. |
| `data/road/road_f_metadata.json` | `ROAD-F` | metadata | ROAD-F metadata with purpose, route-optimizer limitation, coverage summary, known limitations, and sources. |
| `data/road/road_f_realistic_transit_model.json` | `ROAD-F` | auxiliary | Road transit model reference with model id, purpose, routing-engine-duration policy, formula/default parameters, 5 hard gate flags, source, and confidence. |

## Sea Data Files

| File | Block | Role | Contains |
| --- | --- | --- | --- |
| `data/sea/sea_a_confidence_rules.json` | `SEA-A` | confidence | 7 sea DG confidence rules with rule id, field, condition, confidence cap, effect, and confidence. |
| `data/sea/sea_a_coverage_summary.json` | `SEA-A` | coverage | SEA-A parsing and coverage metrics including total structured rows, main DG record count, unique UN numbers, stowage coverage, segregation/handling-code flags, manual-review gaps, excluded rows, and quality checks. |
| `data/sea/sea_a_dg_sea_acceptance.json` | `SEA-A` | main | 2,912 sea dangerous-goods/IMDG records with DG key, UN number, proper shipping name, symbols, hazard class/division, packing group, label codes, packing instructions, air/sea quantity references, stowage fields, segregation notes, sea acceptance status, hard gate, and triggers. |
| `data/sea/sea_a_field_mapping.json` | `SEA-A` | support | 9 field-mapping records from source columns to Cortex target fields with notes. |
| `data/sea/sea_a_metadata.json` | `SEA-A` | metadata | SEA-A metadata with purpose, coverage summary, 5 sources, 5 known limitations, and 3 quality controls. |
| `data/sea/sea_a_parsing_quality_report.json` | `SEA-A` | coverage | SEA-A parsing quality report with name digit violations sample, excluded rows sample, and notes. |
| `data/sea/sea_a_readiness_rules.json` | `SEA-A` | rules | 6 readiness rules for sea DG factors with gate type, condition, decision, message, and confidence. |
| `data/sea/sea_a_stowage_category_mapping.json` | `SEA-A` | support | 10 stowage category mapping records with category, stowage location, cargo/passenger vessel stowage, passenger restrictions, source, and confidence. |
| `data/sea/sea_b_cargo_type_equipment_mapping.json` | `SEA-B` | auxiliary | 9 cargo-type to equipment mapping records with recommended equipment, hard gate checks, planning factors, and confidence. |
| `data/sea/sea_b_confidence_rules.json` | `SEA-B` | confidence | 7 SEA-B confidence rules with applicability, confidence cap, and reason. |
| `data/sea/sea_b_container_fit_rules.json` | `SEA-B` | main | SEA-B combined fit dataset with 14 container/equipment records, 9 cargo type equipment mapping records, dataset metadata, design principle, and FCL/LCL guidance. |
| `data/sea/sea_b_container_specs.json` | `SEA-B` | auxiliary | 14 standalone container spec records with equipment code/name, family, ISO family, dimensions, door dimensions, capacity, max gross, tare, and related specs. |
| `data/sea/sea_b_coverage_summary.json` | `SEA-B` | coverage | SEA-B coverage metrics including equipment records, containerized records, non-container modes, covered families, dimension coverage, payload coverage, mapping count, readiness rules, and confidence rules. |
| `data/sea/sea_b_field_mapping.json` | `SEA-B` | support | 8 Cortex-to-source field mapping records with source and confidence. |
| `data/sea/sea_b_metadata.json` | `SEA-B` | metadata | SEA-B metadata with sources, coverage summary, verified fields, authored fields, estimated/limited fields, known limitations, and confidence policy. |
| `data/sea/sea_b_readiness_rules.json` | `SEA-B` | rules | 9 readiness rules for container/load fit with factor type, condition, decision, confidence impact, source, and confidence. |
| `data/sea/sea_c_port_capability.json` | `SEA-C` | main | 3,838 port capability records with port key, WPI number, UN/LOCODE, port names, country, region, water body, coordinates, draft, container capability, DG handling, cranes, RoRo, bulk capabilities, constraints, and readiness tags. |
| `data/sea/sea_cost_confidence_rules.json` | `SEA-COST` | confidence | 7 SEA-COST confidence rules with condition, confidence cap, and reason. |
| `data/sea/sea_cost_coverage_summary.json` | `SEA-COST` | coverage | 6 coverage metric records for sea cost data. |
| `data/sea/sea_cost_field_mapping.json` | `SEA-COST` | support | 6 field mapping records from Cortex fields to source fields and source types. |
| `data/sea/sea_cost_lane_benchmarks.json` | `SEA-COST` | auxiliary | 7 lane benchmark records with lane family, origin/destination regions, equipment basis, low/high/reference USD values, price date, basis, cost type, and not-a-quote flag. |
| `data/sea/sea_cost_local_charge_examples.json` | `SEA-COST` | auxiliary | 15 local charge examples with country, port/scope, carrier/authority, charge code/name, equipment, amount, currency, unit, effective date, quote validity, and not-a-quote flag. |
| `data/sea/sea_cost_metadata.json` | `SEA-COST` | metadata | SEA-COST metadata with scope, sources, coverage summary, known limitations, and default confidence policy. |
| `data/sea/sea_cost_readiness_rules.json` | `SEA-COST` | rules | 6 readiness rules for sea cost factors with gate type and message. |
| `data/sea/sea_cost_reference.json` | `SEA-COST` | main | Combined sea cost dataset with 7 lane benchmarks, 12 surcharge references, 15 local charge examples, 6 readiness rules, 7 confidence rules, 6 field mappings, and metadata. |
| `data/sea/sea_cost_surcharge_reference.json` | `SEA-COST` | auxiliary | 12 surcharge reference records with category, charge name, applicability, unit, planning range, currency, gate type, not-a-quote flag, live quote requirement, and source. |
| `data/sea/sea_d_carrier_profiles.json` | `SEA-D` | auxiliary | 7 carrier profile records with carrier code/name/type, headquarters region, public network/schedule/booking references, trade lane families, service capability tags, public claims, source refs, and source. |
| `data/sea/sea_d_carrier_trade_lane_reference.json` | `SEA-D` | main | Combined SEA-D dataset with 7 carrier profiles, 10 trade lane families, 7 readiness rules, 7 confidence rules, 7 field mappings, and metadata. |
| `data/sea/sea_d_confidence_rules.json` | `SEA-D` | confidence | 7 confidence rules for carrier/trade lane reference data. |
| `data/sea/sea_d_coverage_summary.json` | `SEA-D` | coverage | 5 SEA-D coverage metric records with notes. |
| `data/sea/sea_d_field_mapping.json` | `SEA-D` | support | 7 field mapping records for carrier and trade lane source fields. |
| `data/sea/sea_d_metadata.json` | `SEA-D` | metadata | SEA-D metadata with scope, sources, coverage summary, known limitations, and 6 v2 paid/live data needs. |
| `data/sea/sea_d_readiness_rules.json` | `SEA-D` | rules | 7 readiness rules with factor type, trigger, logic, Cortex action, and confidence. |
| `data/sea/sea_d_trade_lane_families.json` | `SEA-D` | auxiliary | 10 trade lane family records with code/name, typical service pattern, frequency hint, planning implication, confidence, and confidence cap if missing. |
| `data/sea/sea_f_confidence_rules.json` | `SEA-F` | confidence | 6 maritime document confidence rules with condition, cap, applicability, reason, source, and confidence. |
| `data/sea/sea_f_coverage_summary.json` | `SEA-F` | coverage | SEA-F coverage summary with jurisdiction coverage, documents covered, known gaps, record counts, hard gate count, conditional records, verified requirements, authored records, and official source count. |
| `data/sea/sea_f_field_mapping.json` | `SEA-F` | support | 10 field mapping records for maritime document and border gate data. |
| `data/sea/sea_f_maritime_documents_border_gates.json` | `SEA-F` | main | 11 maritime document/border gate records with document code/name, jurisdiction, mode scope, when required, trigger conditions, deadline, responsible party, hard gate if missing, gate type, Cortex action, and dependencies. Current SEA-F connector does not read this file yet. |
| `data/sea/sea_f_metadata.json` | `SEA-F` | metadata | SEA-F metadata with description, source strategy, coverage summary, confidence policy, 7 sources, 6 known limitations, and 4 integration notes. |
| `data/sea/sea_f_readiness_rules.json` | `SEA-F` | rules | 9 maritime document readiness rules with name, condition, message, related documents, source, and confidence. |
| `data/sea/sea_i_chokepoints.json` | `SEA-I` | auxiliary | 5 standalone chokepoint records with waterway/area, region, risk types, planning impact, readiness action, hard gate flag/condition, planning factor, live validation requirement, and source ids. |
| `data/sea/sea_i_chokepoints_schedule_readiness.json` | `SEA-I` | main | Combined SEA-I dataset with 5 chokepoint records, 7 schedule readiness rules, and metadata. |
| `data/sea/sea_i_confidence_rules.json` | `SEA-I` | confidence | 5 confidence rules for chokepoint and schedule readiness. |
| `data/sea/sea_i_coverage_summary.json` | `SEA-I` | coverage | SEA-I coverage summary with covered chokepoints, cutoff types, cost/risk concepts, live data not covered, and coverage counts. |
| `data/sea/sea_i_field_mapping.json` | `SEA-I` | support | 8 field mapping records for chokepoint/schedule readiness source fields. |
| `data/sea/sea_i_metadata.json` | `SEA-I` | metadata | SEA-I metadata with scope, design principle, coverage summary, confidence policy, 8 sources, 6 known limitations, and 7 paid/live data needs for v2. |
| `data/sea/sea_i_schedule_readiness_rules.json` | `SEA-I` | rules | 7 schedule readiness rules with applicability, required input fields, deadline type, default deadline assumption, hard gate flag/condition, planning factor, readiness message, source, and confidence. |

