/* ORION TRAIL — mechanical failures. Prefix: mech- */
SCENARIOS.push(
{
  id: "mech-001",
  title: "Coolant Loop Rupture",
  category: "mechanical",
  text: "A high-pressure coolant line bursts in engineering. Superheated steam fills the deck and the engine temperature starts a countdown nobody authorized.",
  weight: 2,
  zones: ["inner", "belt", "outer", "deep"],
  choices: [
    {
      label: "Send an engineer in now",
      detail: "Fast fix, dangerous heat",
      requires: { minCrew: 2 },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "Chen wrestles the valve shut, cursing creatively. Engines back to full.",
          effects: { meds: -1 }, repair: { engines: 20 } },
        { chance: 0.3, text: "The valve shears off entirely. Your engineer is scalded badly before the section auto-seals.",
          effects: { meds: -3, water: -10 }, damage: { engines: -15, morale: -10 } }
      ]
    },
    {
      label: "Vent the loop and run on reserve cooling",
      detail: "Cheap now, expensive forever",
      outcomes: [
        { chance: 1, text: "The coolant sublimates into the void, sparkling. Very pretty. Very gone.",
          effects: { water: -15 }, damage: { engines: -20 } }
      ]
    },
    {
      label: "Full stop. Fix it by the book",
      detail: "Costs 3 days of drift",
      outcomes: [
        { chance: 1, text: "Three days of checklists. The manual's authors are dead, but their paranoia holds up.",
          repair: { engines: 30 }, delayDays: 3 }
      ]
    }
  ]
},
{
  id: "mech-002",
  title: "CO₂ Scrubber Cascade Fault",
  category: "mechanical",
  text: "The primary scrubber bank starts rejecting its own filters. The air tastes like a gym sock and the CO₂ curve is doing something unfriendly.",
  sensorWarning: "Air quality telemetry drifting out of tolerance.",
  choices: [
    {
      label: "Cannibalize spares for a rebuild",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.8, text: "Rebuilt and humming. The air tastes like nothing again, which is the good taste.",
          repair: { lifeSupport: 25 } },
        { chance: 0.2, text: "Two of the spares were counterfeit. Some Earth supplier's last scam, still paying out.",
          damage: { lifeSupport: -10, morale: -5 } }
      ]
    },
    {
      label: "Run the backup bank hot",
      detail: "It was not rated for this",
      outcomes: [
        { chance: 0.6, text: "The backup holds, resentfully. You can hear it aging.",
          damage: { lifeSupport: -10 } },
        { chance: 0.4, text: "The backup bank chokes. For six hours everyone breathes carefully and thinks about trees.",
          effects: { oxygen: -12 }, damage: { lifeSupport: -18, morale: -6 } }
      ]
    }
  ]
},
{
  id: "mech-003",
  title: "Water Recycler Biofilm",
  category: "mechanical",
  text: "The water recycler's output has developed a bouquet. Something is alive in the filtration stack, and it has opinions about sharing. The reclaim efficiency graph slopes the wrong way.",
  sensorWarning: "Reclaimed water conductivity creeping above spec.",
  choices: [
    {
      label: "Strip the stack and sterilize it",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.75, text: "Six hours of scraping something that fights back. The recycler runs clean, and nobody discusses what came out of it.",
          repair: { lifeSupport: 10 } },
        { chance: 0.25, text: "The colony has friends further down the line. You lose a tank before the UV lamps win.",
          effects: { water: -15, meds: -2 } }
      ]
    },
    {
      label: "Flush the whole loop with reserve water",
      detail: "Brute force, brute price",
      outcomes: [
        { chance: 1, text: "Brute force works. The reserve gauge disagrees about the price.",
          effects: { water: -20 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Boil it and call it tea",
      outcomes: [
        { chance: 0.6, text: "Nobody dies. Nobody enjoys hydration either.",
          damage: { morale: -5 } },
        { chance: 0.4, text: "Half the crew spends two days in intimate conversation with the head.",
          effects: { meds: -4, food: -10 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "mech-004",
  title: "Bus Bar Fire",
  category: "mechanical",
  text: "A power distribution bus behind panel C-7 chooses violence. Smoke crawls the ceiling in zero-g rivers, and the generator load readout is spiking to numbers with exclamation points.",
  choices: [
    {
      label: "Kill the deck and smother it",
      outcomes: [
        { chance: 1, text: "Darkness, foam, silence. The bus is a sculpture of slag, but the fire is dead.",
          effects: { power: -15 }, damage: { generator: -10 } }
      ]
    },
    {
      label: "Fight it live to save the bus",
      detail: "The mains stay hot",
      requires: { minCrew: 2 },
      outcomes: [
        { chance: 0.6, text: "Vasquez beats the flames back before they reach the mains. The bus survives with cosmetic scarring, like everyone else aboard.",
          damage: { generator: -5 } },
        { chance: 0.25, text: "The foam runs out before the fire does. Burns, screaming, and a bus that dies anyway.",
          effects: { meds: -3 }, damage: { generator: -15, morale: -5 } },
        { chance: 0.15, text: "An arc flash jumps the gap. There is nothing to bury.",
          crewDeath: true, damage: { generator: -20, morale: -15 } }
      ]
    },
    {
      label: "Vent the compartment to space",
      outcomes: [
        { chance: 1, text: "The fire dies gasping, along with a deck's worth of atmosphere. The bus is toast. Efficient, thorough toast.",
          effects: { oxygen: -12 }, damage: { generator: -15 } }
      ]
    }
  ]
},
{
  id: "mech-005",
  title: "Airlock Interlock Failure",
  category: "mechanical",
  text: "Airlock two's interlock computer now believes both doors can be open at once. It is wrong in a way that vents ships. Currently it is only wrong in theory.",
  choices: [
    {
      label: "Weld the outer door shut",
      cost: { parts: -2 },
      outcomes: [
        { chance: 1, text: "Problem solved with fire. The ship has one less exit, and the crew has one more thing to think about at night.",
          damage: { morale: -5 } }
      ]
    },
    {
      label: "Rebuild the interlock logic",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.7, text: "You rewire the safety chain the way the manual would want, if the manual had survived.",
          repair: { hull: 5 } },
        { chance: 0.3, text: "The test cycle blows the inner seal. The ship keeps its air the hard way — barely.",
          effects: { oxygen: -15 }, damage: { hull: -15 } }
      ]
    },
    {
      label: "Post a sign: DO NOT USE",
      outcomes: [
        { chance: 0.7, text: "The sign holds. Engineering by laminated paper.",
          damage: { morale: -5 } },
        { chance: 0.3, text: "Someone uses it anyway. The auto-seal wins the race by half a second, which sounds closer than you'd like.",
          effects: { oxygen: -20 }, damage: { hull: -10, morale: -10 } }
      ]
    }
  ]
},
{
  id: "mech-006",
  title: "Navigation Core Kernel Panic",
  category: "mechanical",
  text: "The astrogation computer dies mid-calculation, taking your position estimate with it. The ship's official location is currently a stack trace.",
  choices: [
    {
      label: "Cold restart from backups",
      detail: "One quiet day of drift",
      outcomes: [
        { chance: 0.8, text: "It boots, thinks hard, and finds the ship exactly where you feared: slightly off course, fixably so.",
          repair: { astrogation: 10 }, delayDays: 1 },
        { chance: 0.2, text: "The backups are corrupted. Of course they are. You rebuild from installation media older than some of the crew.",
          damage: { astrogation: -25 }, delayDays: 2 }
      ]
    },
    {
      label: "Navigate by sextant and stubbornness",
      outcomes: [
        { chance: 0.6, text: "Okafor remembers spherical trigonometry, mostly. The burn corrections are expensive but honest.",
          effects: { fuel: -8 }, damage: { astrogation: -10 } },
        { chance: 0.4, text: "Star sights through a dirty porthole. You find your position two days late and several degrees embarrassed.",
          effects: { fuel: -15 }, damage: { astrogation: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Hot-patch the kernel in flight",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.65, text: "The patch takes. The computer resumes as if nothing happened, which is exactly how you want near-death experiences handled.",
          repair: { astrogation: 15 } },
        { chance: 0.35, text: "The patch cascades into the sensor drivers. Now two computers are lying to you.",
          damage: { astrogation: -20, sensors: -10 } }
      ]
    }
  ]
},
{
  id: "mech-007",
  title: "Capacitor Bank Lockout",
  category: "mechanical",
  text: "The weapons capacitor bank charges itself and refuses to bleed down. A torpedo's worth of energy sits behind a relay that has stopped answering, humming a note you feel in your teeth.",
  choices: [
    {
      label: "Bleed it through the keel ground",
      detail: "Slow, safe, thirsty",
      outcomes: [
        { chance: 1, text: "Twenty minutes of every light aboard dimming in sympathy. Slow, safe, and the power budget takes the bruise.",
          effects: { power: -15 } }
      ]
    },
    {
      label: "Jumper-rig a manual discharge",
      detail: "Bare bus bars, steady hands",
      requires: { minCrew: 2 },
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.6, text: "Diaz walks the jumper across the bus bars like a bomb tech. The bank sighs and lets go.",
          repair: { weapons: 10 } },
        { chance: 0.25, text: "The arc grounds through the rig instead of the resistor. Diaz keeps both hands, technically.",
          effects: { meds: -3 }, damage: { morale: -8 } },
        { chance: 0.15, text: "The bank finds a path nobody drew. The flash is visible from the bridge.",
          crewDeath: true, damage: { weapons: -20, morale: -15 } }
      ]
    },
    {
      label: "Fire it at nothing",
      outcomes: [
        { chance: 1, text: "The bolt sails into the dark, gorgeous and pointless. The tube wasn't rated for an overcharged shot, but morale was.",
          effects: { power: -8 }, damage: { weapons: -8 }, repair: { morale: 5 } }
      ]
    }
  ]
},
{
  id: "mech-008",
  title: "Regolith in the Intakes",
  category: "mechanical",
  zones: ["belt"],
  text: "Belt dust has packed the radiator intakes like grey snow. Engine cooling efficiency drops by the hour, and the dust is patient in a way machinery isn't.",
  choices: [
    {
      label: "EVA and lance the ducts clear",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.7, text: "Two hours of suit work with a nitrogen lance. The intakes gleam. The dust will be back, but so will you.",
          repair: { engines: 10 } },
        { chance: 0.3, text: "A pebble at relative velocity punches through a suit gauntlet. The glove seals. The mood doesn't.",
          effects: { oxygen: -8, meds: -2 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Run the engines hot and burn it out",
      outcomes: [
        { chance: 0.5, text: "Most of it vaporizes. The rest fuses somewhere expensive.",
          damage: { engines: -10 } },
        { chance: 0.5, text: "The dust slags into glass across the cooling fins. Congratulations: you have invented ceramic engine liner, badly.",
          effects: { power: -10 }, damage: { engines: -20 } }
      ]
    },
    {
      label: "Throttle down until the cloud thins",
      outcomes: [
        { chance: 1, text: "You crawl through the dust like a ship apologizing. Slow costs days; days cost everything else.",
          effects: { fuel: -5 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "mech-009",
  title: "Sunward Expansion Joints",
  category: "mechanical",
  zones: ["inner"],
  sensorWarning: "Hull strain gauges cycling harder with every thermal rotation.",
  text: "This close to the sun, the hull runs a hundred-degree fever on one side. The expansion joints creak like an old house, and one seam has started creeping past its tolerance line.",
  choices: [
    {
      label: "Barbecue roll — rotate to even the bake",
      outcomes: [
        { chance: 1, text: "You spin the ship slowly on her long axis. The seams relax. The maneuvering budget doesn't.",
          effects: { fuel: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Shim the seams from inside",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.75, text: "Hand-cut shims, torqued by feel. The old shipwrights would approve, or at least stop laughing.",
          repair: { hull: 10 } },
        { chance: 0.25, text: "A shim pops free under thermal load and takes a weld with it. The hiss is brief but educational.",
          effects: { oxygen: -10 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "The designers rated it for this. Probably.",
      outcomes: [
        { chance: 0.6, text: "The seam holds. It groans about it constantly, like a martyr.",
          damage: { hull: -5 } },
        { chance: 0.4, text: "The seam splits along two meters of weld. The designers, notably, are not aboard.",
          damage: { hull: -20, morale: -10 } }
      ]
    }
  ]
},
{
  id: "mech-010",
  title: "Cryogenic Fuel Line Freeze",
  category: "mechanical",
  zones: ["deep"],
  text: "Out here, even the fuel gets cold feet. A transfer line has iced solid, and the engines are sipping from the wrong tank with a straw.",
  choices: [
    {
      label: "Heat tape and patience",
      outcomes: [
        { chance: 1, text: "Six hours of electric warmth in a place that resents it. The line thaws. The power budget shivers.",
          effects: { power: -15 }, delayDays: 1 }
      ]
    },
    {
      label: "Percussion maintenance",
      detail: "Hit it until the ice cracks",
      outcomes: [
        { chance: 0.6, text: "The ice lets go on the fourth hit. Engineering solutions come in all calibers.",
          effects: { fuel: -3 } },
        { chance: 0.4, text: "The line cracks along with the ice. Fuel sublimates into the dark before the shutoff catches.",
          effects: { fuel: -18 }, damage: { engines: -15 } }
      ]
    },
    {
      label: "Reroute through the backup line",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.8, text: "Valves turn, fuel flows. The backup line was waiting its whole life for this." },
        { chance: 0.2, text: "The backup is frozen too. Frozen all the way down. You wait, burning heater power like a promise.",
          effects: { power: -10 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "mech-011",
  title: "Heater Coil Burnout",
  category: "mechanical",
  zones: ["deep"],
  text: "The cabin heater coils burn out one by one, politely, like candles. Interior temperature is heading somewhere Scandinavian. The dark outside doesn't care, which is the most honest thing about it.",
  choices: [
    {
      label: "Rewind the coils from scrap wire",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "Hand-wound coils, ugly as sin, warm as forgiveness.",
          repair: { lifeSupport: 15 } },
        { chance: 0.3, text: "Your scrap wire shorts on first power-up and takes a breaker cabinet with it.",
          effects: { power: -8 }, damage: { lifeSupport: -10 } }
      ]
    },
    {
      label: "Everyone bunks in the galley",
      outcomes: [
        { chance: 1, text: "One warm room, eleven opinions about snoring. The stove runs constantly, and so does the complaining.",
          effects: { power: 8, food: -8 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Run the emergency resistive heaters",
      outcomes: [
        { chance: 1, text: "Warm, simple, and hungry. The heaters eat watts like the crew eats rations: joylessly and fast.",
          effects: { power: -20 } }
      ]
    }
  ]
},
{
  id: "mech-012",
  title: "Harmonic Shakedown",
  category: "mechanical",
  requires: { systemDamaged: ["engines"] },
  text: "Your wounded engine has developed a vibration, and the vibration has developed a following. Fasteners across three decks are backing out in sympathy. The ship is slowly unscrewing itself.",
  choices: [
    {
      label: "Full stop. Retorque everything",
      outcomes: [
        { chance: 1, text: "Two days with torque wrenches and a checklist of four thousand bolts. Tedium is a repair procedure.",
          repair: { engines: 10 }, delayDays: 2 }
      ]
    },
    {
      label: "Chase the worst rattles only",
      outcomes: [
        { chance: 0.6, text: "You silence the loudest offenders. The quiet ones are still out there, loosening.",
          damage: { hull: -5 } },
        { chance: 0.4, text: "A cargo rack tears free in the night and redistributes a crate of spares into shrapnel.",
          effects: { parts: -3 }, damage: { hull: -15 } }
      ]
    },
    {
      label: "Push through and let it shake",
      outcomes: [
        { chance: 0.5, text: "The vibration finds a new resonance and settles there, gnawing.",
          damage: { engines: -10 } },
        { chance: 0.5, text: "Something in the mounting structure cracks. The sensors misalign in sympathy.",
          damage: { engines: -20, sensors: -10 } }
      ]
    }
  ]
},
{
  id: "mech-013",
  title: "Condensation Cascade",
  category: "mechanical",
  requires: { systemDamaged: ["lifeSupport"] },
  text: "With life support limping, humidity has nowhere to go but everywhere. Water beads on cold bulkheads and drips into places the wiring diagrams call 'dry.' The sensor mains fizz quietly.",
  choices: [
    {
      label: "Run the dehumidifiers flat out",
      outcomes: [
        { chance: 1, text: "The ship dries out over a noisy week. The power meter reads like a confession.",
          effects: { power: -15 } }
      ]
    },
    {
      label: "Conformal-coat the exposed boards",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.75, text: "Every board gets a raincoat. Tedious, sticky, effective.",
          repair: { sensors: 5 } },
        { chance: 0.25, text: "A board dies under the brush — apparently it was only alive out of habit.",
          damage: { sensors: -15 } }
      ]
    },
    {
      label: "Mop and pray",
      outcomes: [
        { chance: 0.5, text: "The towels win some battles. The drips win the war, slowly.",
          damage: { sensors: -10 } },
        { chance: 0.5, text: "Water finds the main sensor bus and then the generator feed, in that order, at 3 a.m.",
          damage: { sensors: -20, generator: -10 } }
      ]
    }
  ]
},
{
  id: "mech-014",
  title: "The Patch That Weeps",
  category: "mechanical",
  requires: { systemDamaged: ["hull"] },
  sensorWarning: "Cabin pressure trending down a tenth of a percent per hour.",
  text: "Your earlier hull patch has started weeping air around its edges. The whistle is faint, almost musical. Leaks that sing tend to graduate.",
  choices: [
    {
      label: "EVA and patch it properly",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.8, text: "Plate, weld, test. The whistle stops. Silence never sounded so expensive.",
          repair: { hull: 20 } },
        { chance: 0.2, text: "The old patch tears free during prep, and the wound underneath is wider than the survey said.",
          effects: { oxygen: -10 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Foam it from inside",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.6, text: "The sealant foam sets hard in the gap. Not pretty, not permanent, not leaking.",
          repair: { hull: 5 } },
        { chance: 0.4, text: "The foam expands into a return vent and sets there too. The leak slows; so does the airflow.",
          effects: { oxygen: -8 }, damage: { lifeSupport: -10 } }
      ]
    },
    {
      label: "Drop cabin pressure to starve the leak",
      outcomes: [
        { chance: 1, text: "Physics obliges: less pressure, less leak. Everyone's ears pop for a week and tempers pop sooner.",
          effects: { oxygen: -15 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "mech-015",
  title: "Brownout Cascade",
  category: "mechanical",
  requires: { systemDamaged: ["generator"] },
  text: "The wounded generator sags every time a heavy load kicks in. Lights dip, fans stall, computers reboot mid-thought. The ship is running on a heartbeat with a murmur.",
  choices: [
    {
      label: "Load-shed: kill everything nonessential",
      outcomes: [
        { chance: 1, text: "The ship goes monastic. Stable power, dim corridors, and a crew discovering how much of morale was just lighting.",
          effects: { power: 10 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Rebuild the voltage regulator",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "New regulator, steady output. The generator still wheezes, but now it wheezes in tune.",
          repair: { generator: 20 } },
        { chance: 0.3, text: "The rebuilt regulator arcs on first load and the whole board goes dark for a day.",
          effects: { power: -12 }, damage: { generator: -10 } }
      ]
    },
    {
      label: "Ride it out",
      outcomes: [
        { chance: 0.5, text: "The brownouts keep chewing. The sensor suite reboots so often it has a boot-screen fan club.",
          damage: { sensors: -10 } },
        { chance: 0.5, text: "A dip hits mid-write and scrambles the astrogation tables. The generator dips further, encouraged.",
          damage: { astrogation: -15, generator: -10 } }
      ]
    }
  ]
},
{
  id: "mech-016",
  title: "Reaction Wheel Whine",
  category: "mechanical",
  sensorWarning: "Reaction wheel three's bearing signature has developed a voice.",
  text: "Reaction wheel three's bearing is singing — a thin rising note every engineer in history has learned to dread. It still works. That's what they all say, right up until.",
  choices: [
    {
      label: "Swap the bearing now",
      detail: "A day of careful hands",
      cost: { parts: -2 },
      outcomes: [
        { chance: 1, text: "The old bearing comes out scored and hot. It had a week left, maybe. Now the ship points where you ask.",
          repair: { astrogation: 10 }, delayDays: 1 }
      ]
    },
    {
      label: "Run it to failure",
      outcomes: [
        { chance: 0.55, text: "The whine climbs half an octave and holds. Pointing accuracy degrades gently, like eyesight.",
          damage: { astrogation: -5 } },
        { chance: 0.45, text: "The wheel seizes at speed. The whole ship lurches; the thrusters spend real fuel arguing it back.",
          effects: { fuel: -10 }, damage: { astrogation: -20 } }
      ]
    },
    {
      label: "Spin it down, fly on three wheels",
      outcomes: [
        { chance: 1, text: "Three wheels do the work of four, with thruster help. Every attitude change now comes with a fuel receipt.",
          effects: { fuel: -12 } }
      ]
    }
  ]
},
{
  id: "mech-017",
  title: "Freezer Failure",
  category: "mechanical",
  text: "The galley freezer dies overnight with three months of protein aboard. The thaw clock is running. The cook looks like someone told him about Earth all over again.",
  choices: [
    {
      label: "Feast week — eat it before it turns",
      outcomes: [
        { chance: 1, text: "Steak for breakfast, steak for dinner, morale through the ceiling. The pantry math afterward is a horror story.",
          effects: { food: -20 }, repair: { morale: 10 } }
      ]
    },
    {
      label: "Rig a heat pump off life support",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "The freezer hums back to life, plumbed into systems it was never introduced to. It works. Don't touch anything." },
        { chance: 0.3, text: "The freezer runs cold, and the cabin air runs strange. Life support was not consulted and files a grievance.",
          damage: { lifeSupport: -10 } }
      ]
    },
    {
      label: "Vacuum-dry the lot",
      outcomes: [
        { chance: 1, text: "Everything becomes jerky. Breakfast is jerky. Soup is jerky, somehow. It keeps forever, and tastes like it too.",
          effects: { food: -10, water: -10 }, damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "mech-018",
  title: "High-Gain Antenna Seizure",
  category: "mechanical",
  zones: ["outer", "deep"],
  text: "The high-gain antenna's drive motor seizes with the dish pointed at a patch of nothing. Long-range sensors are down to a squint. Out here, a squint gets you surprised.",
  choices: [
    {
      label: "EVA with a breaker bar",
      outcomes: [
        { chance: 0.65, text: "Leverage, profanity, and thirty meters of nothing below. The dish swings free and locks onto the beacon grid.",
          effects: { oxygen: -5 }, repair: { sensors: 15 } },
        { chance: 0.35, text: "The bar slips. Osei cracks a rib against the mast, and the dish gains a dent to commemorate it.",
          effects: { meds: -2 }, damage: { sensors: -10, morale: -5 } }
      ]
    },
    {
      label: "Force it with raw bus power",
      outcomes: [
        { chance: 0.5, text: "The motor moves, screaming like it's owed money. The dish tracks again — for now.",
          effects: { power: -10 }, repair: { sensors: 5 } },
        { chance: 0.5, text: "The winding burns out with a smell that finds every deck. Now it's seized and dead.",
          effects: { power: -10 }, damage: { sensors: -20 } }
      ]
    },
    {
      label: "Fly on the low-gain and hope",
      outcomes: [
        { chance: 1, text: "The low-gain array sees the universe as smudges. You will be reacting late to everything for a while.",
          damage: { sensors: -15 } }
      ]
    }
  ]
},
{
  id: "mech-019",
  title: "Waste Line Blockage",
  category: "mechanical",
  text: "The main waste reclamation line blocks somewhere behind the deck plating, and pressure is building in a system nobody wants pressurized. Life support's most ignored subsystem demands attention the way it always does: all at once.",
  choices: [
    {
      label: "Someone goes in with a snake and gloves",
      outcomes: [
        { chance: 1, text: "Ramirez draws the short straw. Ramirez always draws the short straw. The line runs clear; Ramirez doesn't speak at dinner.",
          repair: { lifeSupport: 10 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Overpressure flush from the clean side",
      outcomes: [
        { chance: 0.6, text: "The blockage surrenders to physics. The water bill is ugly, but nobody had to meet the problem personally.",
          effects: { water: -10 } },
        { chance: 0.4, text: "The line ruptures behind a wall panel. What follows is not described in any manual, and never will be.",
          effects: { water: -15 }, damage: { lifeSupport: -15, morale: -10 } }
      ]
    },
    {
      label: "Seal that branch, halve the plumbing",
      outcomes: [
        { chance: 1, text: "Half the heads go offline. The queue forms immediately, and so do the opinions. Water use drops, at least.",
          effects: { water: 5 }, damage: { morale: -15 } }
      ]
    }
  ]
},
{
  id: "mech-020",
  title: "Injector Drift",
  category: "mechanical",
  text: "The fuel injectors have drifted out of calibration, and the engines are running rich, burning fuel you'll want at the far end. The exhaust glows a color the manual calls 'wasteful.'",
  choices: [
    {
      label: "Recalibrate under thrust",
      outcomes: [
        { chance: 0.7, text: "Delicate work on a live engine. The burn steadies to textbook color.",
          repair: { engines: 5 } },
        { chance: 0.3, text: "An injector backfires mid-adjustment. The combustion chamber takes it personally.",
          effects: { fuel: -8 }, damage: { engines: -15 } }
      ]
    },
    {
      label: "Shut down and bench-strip the injectors",
      cost: { parts: -2 },
      outcomes: [
        { chance: 1, text: "Two days adrift with the injectors laid out like surgery. Reassembled, they purr. Best fuel economy of the voyage.",
          repair: { engines: 15 }, delayDays: 2 }
      ]
    },
    {
      label: "Live with it",
      outcomes: [
        { chance: 1, text: "The ship drinks like it's grieving. Every day the fuel gauge drops a little faster than the map does.",
          effects: { fuel: -15 } }
      ]
    }
  ]
}
);
