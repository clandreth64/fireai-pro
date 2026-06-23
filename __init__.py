{
  "id": "ordinary_retail",
  "description": "Ordinary-1 retail sales floor",
  "ctx": {
    "occupancy": "retail sales",
    "total_area": 20000,
    "ceiling_height": 14,
    "static_pressure": 85,
    "residual_pressure": 72,
    "water_supply_flow": 2000,
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
