/* ORION TRAIL — cosmic hazards. Prefix: haz- */
SCENARIOS.push(
{
  id: "haz-001",
  title: "Micrometeoroid Swarm",
  category: "cosmic-hazards",
  text: "The forward array picks up a debris field spread across your corridor — millions of gravel-sized bullets, each one moving faster than an apology.",
  weight: 2,
  zones: ["belt", "outer"],
  sensorWarning: "Particulate density rising along projected course.",
  choices: [
    {
      label: "Burn hard around the field",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.85, text: "A long, expensive detour. The hull stays boring, which is the best thing a hull can be.",
          effects: {} },
        { chance: 0.15, text: "The field is wider than charted. You clip the edge anyway, having paid for the privilege.",
          damage: { hull: -10 } }
      ]
    },
    {
      label: "Thread it slow, nose-first",
      detail: "Smallest cross-section, longest hour",
      outcomes: [
        { chance: 0.5, text: "An hour of pings and held breath. The armor plating earns its mass.",
          damage: { hull: -5 } },
        { chance: 0.5, text: "Three sharp cracks. A sensor mast sheers off and the hull takes ugly gouges.",
          damage: { hull: -20, sensors: -15 } }
      ]
    }
  ]
},
{
  id: "haz-002",
  title: "X-Class Flare",
  category: "cosmic-hazards",
  text: "The sun clears its throat. An X-class flare is eight minutes out — meaning it already happened, and the light only just arrived to tell you the hard radiation is right behind it.",
  weight: 2,
  zones: ["inner"],
  sensorWarning: "Solar activity spiking. Flare probability climbing past comfortable.",
  choices: [
    {
      label: "Crew into the water-tank corridor",
      detail: "The electronics fend for themselves",
      outcomes: [
        { chance: 0.7, text: "The crew emerges pale and unroasted. The sensor package took the storm personally.",
          damage: { sensors: -10 }, delayDays: 1 },
        { chance: 0.3, text: "A long ugly storm. Sensors and the reactor's control runs both come out scorched.",
          damage: { sensors: -15, generator: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Keep flying, crew at stations",
      outcomes: [
        { chance: 0.6, text: "Everyone takes their anti-rad doses and their chances. Nausea all around, nothing worse.",
          effects: { meds: -3 } },
        { chance: 0.4, text: "Dosimeters run hot. Two crew spend the week vomiting on schedule, and morale files a complaint.",
          effects: { meds: -4 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Turn the engines sunward as a shadow shield",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.8, text: "The drive section eats the worst of it. Engines were built for abuse; sensors less so.",
          damage: { sensors: -5 } },
        { chance: 0.2, text: "Holding attitude against the flare costs you a thruster and a sensor cluster anyway.",
          damage: { engines: -10, sensors: -5 } }
      ]
    }
  ]
},
{
  id: "haz-003",
  title: "Coronal Mass Ejection",
  category: "cosmic-hazards",
  text: "A wall of solar plasma the size of forty Earths is billowing outward, and your course runs through its shoulder. It isn't aiming at you. It doesn't have to.",
  zones: ["inner"],
  sensorWarning: "Coronal ejection detected. Plasma front will intersect projected course.",
  choices: [
    {
      label: "Burn hard for the thin flank",
      cost: { fuel: -12 },
      outcomes: [
        { chance: 0.75, text: "You outrun the shoulder by a margin that looks fine in the log and felt like nothing at the time.",
          effects: {} },
        { chance: 0.25, text: "The front is faster than charted. The edge of it drags across the ship like a live wire.",
          effects: { power: -10 }, damage: { generator: -10 } }
      ]
    },
    {
      label: "Batten down and take it amidships",
      outcomes: [
        { chance: 0.5, text: "Hours of induced current singing through every conductor aboard. The generator ages years.",
          effects: { power: -15 }, damage: { generator: -15 } },
        { chance: 0.5, text: "Breakers fail in sequence like dominoes that cost money. The board goes half dark.",
          effects: { power: -20 }, damage: { generator: -25, sensors: -10 } }
      ]
    },
    {
      label: "Go dark and drift through",
      detail: "Nothing energized, nothing to fry",
      outcomes: [
        { chance: 0.8, text: "Two days cold and silent while the storm walks over you. Life support on a whisper.",
          effects: { oxygen: -5 }, delayDays: 2 },
        { chance: 0.2, text: "The restart is uglier than the storm. The generator resents being turned off almost as much as being fried.",
          effects: { oxygen: -5 }, damage: { generator: -10 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "haz-004",
  title: "Radiators at Redline",
  category: "cosmic-hazards",
  text: "Perihelion leg. The radiators glow dull orange, dumping heat as fast as physics allows, which is suddenly not fast enough. Somewhere aft, something expensive starts to smell like solder.",
  zones: ["inner"],
  sensorWarning: "Thermal projections exceed radiator capacity for the next leg.",
  choices: [
    {
      label: "Vent water for evaporative cooling",
      cost: { water: -10 },
      outcomes: [
        { chance: 0.85, text: "The ship exhales a comet's tail of steam and the temperature graphs stop climbing. Expensive sweat.",
          effects: {} },
        { chance: 0.15, text: "A vent valve jams open and keeps venting after the crisis passes. The plumbing owes you an apology.",
          effects: { water: -5 }, damage: { hull: -5 } }
      ]
    },
    {
      label: "Overclock the coolant loops",
      cost: { power: -12 },
      outcomes: [
        { chance: 0.7, text: "The pumps scream at frequencies that upset the dog you don't have. The heat moves. Barely.",
          effects: {} },
        { chance: 0.3, text: "A pump bearing gives out at full load, and the generator takes the shock as a personal insult.",
          damage: { generator: -15 } }
      ]
    },
    {
      label: "Ride it hot and pray",
      outcomes: [
        { chance: 0.4, text: "The ship groans, ticks, and holds. Design margins exist so you can spend them like this.",
          effects: {} },
        { chance: 0.35, text: "The sensor electronics cook first. Delicate things always do.",
          damage: { sensors: -10 } },
        { chance: 0.25, text: "A scrubber assembly warps in the heat. The air gets thick before anyone finds the fault.",
          effects: { oxygen: -5 }, damage: { lifeSupport: -15 } }
      ]
    }
  ]
},
{
  id: "haz-005",
  title: "Kessler's Graveyard",
  category: "cosmic-hazards",
  text: "A mining barge died here years ago and its debris has been multiplying ever since — collision breeding collision, a cloud of jagged metal wearing the barge's name. Your corridor runs straight through the estate.",
  weight: 2,
  zones: ["belt"],
  sensorWarning: "Manufactured debris field ahead. Fragment count in the thousands and climbing.",
  choices: [
    {
      label: "Detour under the ecliptic",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 0.9, text: "A long swing beneath the wreckage. The graveyard glitters overhead, politely distant.",
          effects: {} },
        { chance: 0.1, text: "One stray fragment found its way down here years ago, apparently just to wait for you.",
          damage: { hull: -5 } }
      ]
    },
    {
      label: "Tuck in behind a hull slab and follow it through",
      detail: "Let the dead barge take the hits",
      outcomes: [
        { chance: 0.55, text: "The slab eats a dozen impacts on your behalf. The dead protecting the living. Only fair.",
          damage: { hull: -5 } },
        { chance: 0.45, text: "Halfway through, the slab sheds — and everything it was blocking arrives at once.",
          damage: { hull: -20, sensors: -10 } }
      ]
    },
    {
      label: "Hold position and let the cloud pass",
      outcomes: [
        { chance: 0.8, text: "Two days watching scrap tumble by. The debris moves on to disappoint someone else.",
          delayDays: 2 },
        { chance: 0.2, text: "The trailing edge is wider than the leading edge. You learn this experimentally.",
          damage: { hull: -10 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "haz-006",
  title: "Rock on Intercept",
  category: "cosmic-hazards",
  text: "One contact, dead ahead, the size of a small warehouse. It has been tumbling along this exact line since before your species had fire, and it is not going to be the one that blinks.",
  zones: ["belt"],
  sensorWarning: "Single large contact, closing, indifferent.",
  choices: [
    {
      label: "Hard lateral burn",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.85, text: "The rock sails past close enough to read its history in the craters. It never noticed you.",
          effects: {} },
        { chance: 0.15, text: "You dodge the warehouse and meet its luggage — a companion rock riding the same line.",
          damage: { hull: -10 } }
      ]
    },
    {
      label: "Shatter it with the point-defense guns",
      detail: "Turn one problem into gravel",
      requires: { systemOk: ["weapons"] },
      cost: { power: -8 },
      outcomes: [
        { chance: 0.6, text: "The rock comes apart cleanly. You fly through a haze of its remains, ticking like hail.",
          damage: { hull: -5 } },
        { chance: 0.4, text: "It splits into three big pieces instead of a thousand small ones. Two of them find you.",
          damage: { hull: -15, sensors: -10 } }
      ]
    },
    {
      label: "Brace for impact",
      outcomes: [
        { chance: 0.3, text: "A glancing blow along the dorsal plating. The ship rings like a struck bell for an hour.",
          damage: { hull: -15 } },
        { chance: 0.5, text: "Solid hit. The hull holds, in the sense that most of it is still attached.",
          damage: { hull: -25 } },
        { chance: 0.2, text: "It hits square. A compartment breaches and vents before the seals catch.",
          effects: { oxygen: -10 }, damage: { hull: -35 } }
      ]
    }
  ]
},
{
  id: "haz-007",
  title: "The Radiation Moat",
  category: "cosmic-hazards",
  text: "The gas giant's magnetosphere is a cathedral of trapped particles, and the fastest route runs through the nave. Your dosimeters are already muttering. The planet wears its radiation the way castles wore moats.",
  zones: ["outer"],
  sensorWarning: "Charged-particle flux rising along the inner corridor.",
  choices: [
    {
      label: "Sprint the gap at full burn",
      cost: { fuel: -12 },
      outcomes: [
        { chance: 0.8, text: "Minimum time in the moat. The dose is real but survivable, like most of your decisions.",
          effects: { meds: -2 } },
        { chance: 0.2, text: "A flux spike catches you mid-corridor. The crew gets a year's dose, the sensors get worse.",
          effects: { meds: -5 }, damage: { sensors: -10 } }
      ]
    },
    {
      label: "Coast through on the shielded axis",
      detail: "Slow, cheap, and glowing",
      outcomes: [
        { chance: 0.5, text: "Days of headaches and metallic mouths. The med locker earns its keep.",
          effects: { meds: -6 } },
        { chance: 0.35, text: "Radiation sickness works through the crew in shifts. Nobody sleeps. Everybody snaps.",
          effects: { meds: -8 }, damage: { morale: -10 } },
        { chance: 0.15, text: "One dosimeter reads a number the manual calls 'not compatible with continued duty.' The manual is being polite.",
          effects: { meds: -6 }, crewDeath: true }
      ]
    },
    {
      label: "Loop wide around the belts",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.85, text: "The long way around. The planet shrinks behind you, keeping its moat and its opinion.",
          delayDays: 3 },
        { chance: 0.15, text: "The magnetopause is lumpier than charted, and the detour grows a detour of its own.",
          delayDays: 5 }
      ]
    }
  ]
},
{
  id: "haz-008",
  title: "Ring-Plane Crossing",
  category: "cosmic-hazards",
  text: "The rings look serene from ten degrees above — a billion tonnes of ice arranged with terrifying politeness. You have to cross the plane somewhere. The ice does not care where.",
  zones: ["outer"],
  sensorWarning: "Ring plane ahead reads dense — particles from dust-grain to house-sized.",
  choices: [
    {
      label: "Thread the charted gap",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.9, text: "Through the gap, clean. On either side, walls of ice slide past like patient teeth.",
          effects: {} },
        { chance: 0.1, text: "A shepherd moonlet has been redecorating. A strand of ring material crosses the gap, and you.",
          damage: { hull: -10 } }
      ]
    },
    {
      label: "Punch through the thin band",
      outcomes: [
        { chance: 0.45, text: "Ninety seconds of hissing ice against the hull, like driving through someone else's blizzard.",
          damage: { hull: -5 } },
        { chance: 0.35, text: "Something fist-sized comes through at four kilometers a second. The hull objects formally.",
          damage: { hull: -15, sensors: -10 } },
        { chance: 0.2, text: "A boulder you never saw shears a gash along the flank. The breach compartment is packed with ring ice. Silver linings, literally frozen.",
          effects: { water: 8 }, damage: { hull: -25 } }
      ]
    },
    {
      label: "Ride the plane and run the ice scoops",
      detail: "Free water, sold at hull prices",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.6, text: "The tanks drink a comet's worth of clean ice. The hull collects a matching set of dents.",
          effects: { water: 15 }, damage: { hull: -5 } },
        { chance: 0.4, text: "The scoop mouth jams on a chunk and the ship threshes through ice it should have dodged.",
          effects: { water: 8 }, damage: { hull: -15, sensors: -5 } }
      ]
    }
  ]
},
{
  id: "haz-009",
  title: "The Cold Ledger",
  category: "cosmic-hazards",
  text: "Out here, sunlight is a rumor. Hull temperature is dropping past design margins, the heaters are losing gracefully, and frost is growing on the inside of the ship — which is the wrong side.",
  zones: ["outer"],
  sensorWarning: "External temperature falling off the bottom of the chart.",
  choices: [
    {
      label: "Divert the reactor to heating",
      cost: { power: -15 },
      outcomes: [
        { chance: 0.85, text: "The ship warms to merely miserable. The frost retreats to the corners, plotting.",
          effects: {} },
        { chance: 0.15, text: "The generator runs flat out for days and develops a new noise. New noises are never free.",
          damage: { generator: -10 } }
      ]
    },
    {
      label: "Abandon the outboard sections, heat only the core",
      outcomes: [
        { chance: 0.6, text: "The outer decks freeze solid. A water line splits in the dark, quietly, generously.",
          effects: { water: -8 } },
        { chance: 0.4, text: "Thermal contraction opens seams the builders swore were impossible. The cold keeps receipts.",
          effects: { water: -12 }, damage: { hull: -10, lifeSupport: -5 } }
      ]
    },
    {
      label: "Burn fuel in the emergency chemical heaters",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.9, text: "Honest fire in a metal box, like your ancestors. The ship warms. The fuel gauge mourns.",
          effects: {} },
        { chance: 0.1, text: "The heater draw runs richer than spec and the purge cycle eats breathing air to compensate.",
          effects: { oxygen: -5 } }
      ]
    }
  ]
},
{
  id: "haz-010",
  title: "Dust at Speed",
  category: "cosmic-hazards",
  text: "At interstellar velocity, a dust grain carries the energy of a rifle round. The forward counter says the 'empty' in empty space has been overselling itself. The grains have waited a billion years for this. They're patient. You're not.",
  weight: 2,
  zones: ["deep"],
  sensorWarning: "Forward particle count climbing. Density gradient ahead.",
  choices: [
    {
      label: "Energize the deflection screens",
      cost: { power: -12 },
      outcomes: [
        { chance: 0.8, text: "The screens shrug the dust into a glowing bow wave. Beautiful, if you don't check the power ledger.",
          effects: {} },
        { chance: 0.2, text: "A grain too big to deflect arcs across the screen emitters on its way through the sensor mast.",
          damage: { sensors: -10 } }
      ]
    },
    {
      label: "Decelerate below the erosion threshold",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 0.85, text: "Slow and steady. The dust taps the hull like rain on a tin roof instead of gunfire.",
          delayDays: 2 },
        { chance: 0.15, text: "The cloud is deeper than the counters guessed. You crawl through it for most of a week.",
          delayDays: 4 }
      ]
    },
    {
      label: "Hold velocity and take the sandblast",
      outcomes: [
        { chance: 0.5, text: "The bow plating comes out matte-finished, a few millimeters thinner. The void does free abrasion work.",
          damage: { hull: -10 } },
        { chance: 0.35, text: "The forward sensor cluster is ground down to expensive gravel along with a layer of hull.",
          damage: { hull: -15, sensors: -15 } },
        { chance: 0.15, text: "One grain the size of a peppercorn hits like artillery. The forward array simply isn't there anymore.",
          effects: { power: -5 }, damage: { hull: -25, sensors: -20 } }
      ]
    }
  ]
},
{
  id: "haz-011",
  title: "Cosmic Ray Sleet",
  category: "cosmic-hazards",
  text: "Something enormous died far away, a long time ago, and its shrapnel — relativistic nuclei sleeting through everything — has finally reached your neighborhood. The hull slows the particles down mostly as a courtesy.",
  zones: ["deep"],
  sensorWarning: "Cosmic ray background stepping up. Something distant died violently.",
  choices: [
    {
      label: "Pack the crew into the water-tank shelter",
      outcomes: [
        { chance: 0.75, text: "A day of eight people in a corridor built for two, breathing each other's opinions.",
          damage: { morale: -5 }, delayDays: 1 },
        { chance: 0.25, text: "The storm runs long. Two days in the tank corridor, and the rations don't stretch politely.",
          effects: { food: -3 }, damage: { morale: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Keep flying on prophylactic anti-rads",
      cost: { meds: -4 },
      outcomes: [
        { chance: 0.7, text: "The pills do their ugly chemical work. Everyone's fine, for values of fine involving hair loss.",
          effects: {} },
        { chance: 0.2, text: "The dose runs hotter than the pills can chase. The med locker gets another workout.",
          effects: { meds: -3 }, damage: { morale: -5 } },
        { chance: 0.1, text: "One crew member's luck runs out at the cellular level. Nothing to treat. Nothing to fight. Just arithmetic.",
          crewDeath: true }
      ]
    },
    {
      label: "Trust the hull and change nothing",
      outcomes: [
        { chance: 0.5, text: "The sleet passes through you and onward into forever. The dosimeters log it with disapproval.",
          effects: {} },
        { chance: 0.3, text: "A rough week of radiation headaches works through the crew, and the meds go with it.",
          effects: { meds: -5 } },
        { chance: 0.2, text: "Bit-flips cascade through the sensor computers. The crew gets sick; the electronics get senile.",
          effects: { meds: -6 }, damage: { sensors: -10 } }
      ]
    }
  ]
},
{
  id: "haz-012",
  title: "The Charged Hull",
  category: "cosmic-hazards",
  text: "Crossing the current sheet, the hull picks up differential charge — kilovolts of it, looking for a way home. St. Elmo's fire crawls the antenna masts. You are now the largest capacitor for a million kilometers, and capacitors discharge.",
  zones: ["outer", "deep"],
  sensorWarning: "Hull potential climbing. Plasma environment is unusually generous.",
  choices: [
    {
      label: "EVA to rig a grounding boom",
      detail: "Someone walks outside holding a lightning rod",
      requires: { minCrew: 2 },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.75, text: "The boom bleeds the charge off in a slow violet whisper. The EVA suit smells of ozone for a week.",
          effects: {} },
        { chance: 0.25, text: "The hull discharges through the boom mid-installation. The engineer lives, minus some certainty.",
          effects: { meds: -3 }, damage: { sensors: -5 } }
      ]
    },
    {
      label: "Let it discharge on its own schedule",
      outcomes: [
        { chance: 0.4, text: "It crackles off harmlessly over an hour, a private aurora. The void occasionally shows mercy. Occasionally.",
          effects: {} },
        { chance: 0.35, text: "It arcs through the sensor mast, which was the tallest thing around. Lightning has traditions.",
          damage: { sensors: -15 } },
        { chance: 0.25, text: "The discharge finds the main bus. Every light aboard blinks once, like the ship flinching.",
          effects: { power: -10 }, damage: { generator: -15, sensors: -10 } }
      ]
    },
    {
      label: "Vent thruster gas to bleed the charge",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.85, text: "The ionized plume carries the charge away into the dark, where it can bother nobody.",
          effects: {} },
        { chance: 0.15, text: "The plume arcs back along its own trail on the way out, singeing a sensor head in farewell.",
          damage: { sensors: -5 } }
      ]
    }
  ]
},
{
  id: "haz-013",
  title: "The One You Never Saw",
  category: "cosmic-hazards",
  text: "The first warning is the sound — a gunshot through three decks. With the sensor mast dead, nothing announced the rock; it introduced itself personally. There is a hole in cargo bay two, and the atmosphere is leaving without filing paperwork.",
  zones: ["belt", "outer"],
  requires: { systemDamaged: ["sensors"] },
  choices: [
    {
      label: "All hands to patch the breach",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.8, text: "Twenty loud minutes with plate and sealant. The whistle dies. So does nobody, today.",
          effects: { oxygen: -5 }, damage: { hull: -10 } },
        { chance: 0.2, text: "The first patch blows out under pressure test. The second holds, after the bay coughs up more air.",
          effects: { oxygen: -10 }, damage: { hull: -15 } }
      ]
    },
    {
      label: "Seal the bay and write off its contents",
      outcomes: [
        { chance: 0.7, text: "The bulkhead closes on a month of supplies. The hole keeps the bay, and everything in it.",
          effects: { food: -10, oxygen: -5 }, damage: { hull: -10 } },
        { chance: 0.3, text: "The bulkhead seal is older than it looked. It weeps air for days, and the bay keeps the groceries anyway.",
          effects: { food: -15, oxygen: -8 }, damage: { hull: -10, morale: -5 } }
      ]
    },
    {
      label: "Quick-set foam and hope",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.5, text: "The foam bulges, holds, and hardens into an ugly gray scar. Add it to the collection.",
          effects: { oxygen: -5 }, damage: { hull: -10 } },
        { chance: 0.5, text: "The foam plug lets go during the night shift. Round two costs more air and a day of drift.",
          effects: { oxygen: -12 }, damage: { hull: -15 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "haz-014",
  title: "Storm Without a Forecast",
  category: "cosmic-hazards",
  text: "With the flare monitors dark, the first you learn of the solar storm is every dosimeter aboard screaming in unison. The radiation has been coming through for an hour. It didn't knock. Blind ships take the weather personally.",
  zones: ["inner"],
  requires: { systemDamaged: ["sensors"] },
  choices: [
    {
      label: "Mass anti-rad treatment, right now",
      cost: { meds: -6 },
      outcomes: [
        { chance: 0.7, text: "The whole crew on the protocol at once. It works, mostly, and everyone hates you politely.",
          damage: { morale: -5 } },
        { chance: 0.2, text: "Two crew react badly to the drugs on top of the dose. The med locker keeps hemorrhaging.",
          effects: { meds: -3 }, damage: { morale: -10 } },
        { chance: 0.1, text: "For one of the crew, the hour of unshielded storm was already an hour too many. The drugs arrive as a formality.",
          effects: { meds: -2 }, crewDeath: true }
      ]
    },
    {
      label: "Shelter first, triage after",
      outcomes: [
        { chance: 0.6, text: "A day wedged in the tank corridor while the storm finishes. The late doses still sting.",
          effects: { meds: -3 }, damage: { morale: -5 }, delayDays: 1 },
        { chance: 0.4, text: "The storm runs two days, and the untreated symptoms bloom in the dark: fevers, nosebleeds, gallows arithmetic.",
          effects: { meds: -5 }, damage: { morale: -10 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "haz-015",
  title: "The Waking Comet",
  category: "cosmic-hazards",
  text: "A dead comet, sun-warmed for the first time in ten thousand years, is boiling. Jets of gas and gravel vent from its flanks like the whole thing is trying to unscrew itself. Your course grazes the plume.",
  zones: ["belt", "outer"],
  sensorWarning: "Volatile plume ahead — something old and icy is boiling.",
  choices: [
    {
      label: "Skirt the plume upwind",
      cost: { fuel: -7 },
      outcomes: [
        { chance: 0.85, text: "You round the nucleus on its quiet side. It tumbles on, shedding its ancient inventory at nobody.",
          effects: {} },
        { chance: 0.15, text: "The comet's rotation swings a fresh jet across your path like a garden hose full of gravel.",
          damage: { hull: -10, sensors: -5 } }
      ]
    },
    {
      label: "Fly the coma with scoops open",
      detail: "Ten-thousand-year-old ice, free with abrasion",
      cost: { power: -6 },
      outcomes: [
        { chance: 0.55, text: "The tanks fill with primordial volatiles. The hull picks up a finish the yard would call 'distressed.'",
          effects: { water: 12, oxygen: 5 }, damage: { hull: -5 } },
        { chance: 0.45, text: "The gas comes with grit, at velocity. You gain water and lose paint, plating, and a sensor head.",
          effects: { water: 5 }, damage: { hull: -15, sensors: -10 } }
      ]
    },
    {
      label: "Cut straight through the tail",
      outcomes: [
        { chance: 0.6, text: "Dust taps along the hull like rain. The tail glows behind you, indifferent and briefly beautiful.",
          damage: { hull: -5 } },
        { chance: 0.4, text: "A pocket of dense ejecta rattles through the tail. The hull and the forward optics take the roll call.",
          damage: { hull: -15, sensors: -10 } }
      ]
    }
  ]
}
);
