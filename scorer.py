{
  "id": "esfr_warehouse",
  "description": "ESFR warehouse storage, adequate supply",
  "ctx": {
    "occupancy": "warehouse storage",
    "total_area": 60000,
    "ceiling_height": 30,
    "static_pressure": 95,
    "residual_pressure": 82,
    "water_supply_flow": 2500,
    "seismic_zone": "D1",
    "pipe_material": "Schedule 40 Steel"
  },
  "expect": {
    "compliant": true,
    "max_critical_flags": 0,
    "must_pass_sections": ["§8.5.2", "§22"],
    "metrics": { "total_sprinklers": { "min": 1 } }
  }
}
