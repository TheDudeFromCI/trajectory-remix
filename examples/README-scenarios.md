# Generation scenarios

The standard `user_scenario` and `idle_scenario` flows are bundled in
`trajectory_remix/scenario_templates` and load automatically. Both use bounded
content-first generation without local semantic validation. Use a custom
`--scenarios-dir` only to replace the bundled choices with your own flows.

Run validation separately with **Edit → Generation → Validate Trajectory**.
The retired generator and demonstration flows are not selectable generation
scenarios.
