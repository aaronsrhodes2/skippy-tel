# physics-mcp (placeholder)

**Status:** scaffolded, not implemented. `/starmap` in `services/flask-api/`
returns `501 not_implemented` until this exists.

## Future scope

A general physics service. Tell it what matter (mass, composition), how
much, its velocity, its temperature, etc. — it computes anything
matter- or energy-related downstream. Likely usable for AR games as
well as the Skippy Star Map module.

Initial concrete need: celestial ephemeris for `/starmap`. Compute
`{ name, type, az_deg, alt_deg, mag }` for stars and planets above
the horizon at a given `lat`, `lng`, `t_iso`. Likely backed by
[skyfield](https://rhodesmill.org/skyfield/) for ephemeris.

## When to wire this up

When the SkippyStarMap project is ready to consume `/starmap` for real.
Until then this directory exists only to hold the slot.
