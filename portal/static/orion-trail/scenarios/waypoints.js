/* ORION TRAIL — waypoint set-pieces. Prefix: wp- . One per waypoint id via `waypoint:` field. */
SCENARIOS.push(
{
  id: "wp-luna",
  title: "Luna Staging Yards",
  category: "waypoints",
  waypoint: "luna",
  text: "The Yards are a scrapyard pretending to be a port. Refugees crowd the docking ring — families with sealed suitcases and nowhere to point them. A dock boss offers deals; the crowd offers eyes.",
  choices: [
    {
      label: "Take on two refugees with useful trades",
      detail: "More hands, more mouths",
      requires: { resources: { food: 30 } },
      outcomes: [
        { chance: 0.8, text: "A hydroponicist and a welder climb aboard with everything they own in two bags. The crew finds them bunks without being asked.",
          effects: { crew: 2 }, repair: { morale: 6 } },
        { chance: 0.2, text: "They're good people. One of them also has a cough she swears is 'lunar dust.' The doctor starts a file.",
          effects: { crew: 2, meds: -2 } }
      ]
    },
    {
      label: "Trade ship parts for provisions",
      requires: { resources: { parts: 4 } },
      cost: { parts: -3 },
      outcomes: [
        { chance: 1, text: "The dock boss strips your spares with loving efficiency and pays in canned goods and clean water. Fair, by scrapyard standards.",
          effects: { food: 20, water: 15 } }
      ]
    },
    {
      label: "Refuel fast and go before the crowd gets ideas",
      outcomes: [
        { chance: 1, text: "You're cast off within the hour. In the aft camera, the docking ring is a wall of faces you will not describe in the log.",
          effects: { fuel: 8 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "wp-mars",
  title: "The Arcadia Ruins",
  category: "waypoints",
  waypoint: "mars",
  text: "Arcadia Colony answered Earth's collapse with its own, faster. The domes are cracked eggs half-buried in red dust. Somewhere down there: intact stores, and whatever cracked the domes.",
  choices: [
    {
      label: "Send a salvage lander to the storage ring",
      requires: { minCrew: 3 },
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.6, text: "The team returns dust-caked and rich: sealed rations, medical stock, a working recycler pump.",
          effects: { food: 18, meds: 3, parts: 2 } },
        { chance: 0.25, text: "The storage ring was looted years ago. The team brings back scrap and a colonist's diary nobody will admit to reading.",
          effects: { parts: 1 }, damage: { morale: -5 } },
        { chance: 0.15, text: "A dome section subsides under the lander. It limps back to orbit venting and short one passenger.",
          crewDeath: true, damage: { morale: -10 } }
      ]
    },
    {
      label: "Survey from orbit only",
      requires: { systemOk: ["sensors"] },
      outcomes: [
        { chance: 1, text: "High-resolution sweeps find a small unlooted depot beacon and flag it for the desperate. You take the safe, thin skim from an automated silo.",
          effects: { food: 8, water: 6 } }
      ]
    },
    {
      label: "Burn past. Mars already ate one civilization",
      outcomes: [
        { chance: 1, text: "Red recedes. The crew watches the ruins go with the particular quiet of people updating their odds.",
          damage: { morale: -4 } }
      ]
    }
  ]
}
);

/* Gated alternates come BEFORE the generic set-piece for the same waypoint —
   the engine picks the FIRST unused eligible scenario per waypoint. */
SCENARIOS.push(
{
  id: "wp-ceres-holed",
  title: "Ceres Freeport: Dock 9",
  category: "waypoints",
  waypoint: "ceres",
  requires: { systemDamaged: ["hull"] },
  text: "You limp into Ceres Freeport trailing atmosphere, and the strike doesn't care. Pickets seal every union dry dock. Only Dock 9 works — a scab crew welding by torchlight, pricing repairs like ransom. The pickets watch your airlock, waiting to see what kind of ship you are.",
  choices: [
    {
      label: "Pay Dock 9's ransom rate",
      detail: "Fast, competent, despised",
      cost: { food: -15, water: -10 },
      outcomes: [
        { chance: 0.75, text: "The scabs work like the guilty — fast, silent, flawless. Your hull seals. The pickets memorize your registry.",
          repair: { hull: 30 } },
        { chance: 0.25, text: "Good welds, rushed seams. One patch will need redoing somewhere darker, with your own spares.",
          repair: { hull: 18 }, effects: { parts: -2 } }
      ]
    },
    {
      label: "Patch it yourselves under picket eyes",
      cost: { parts: -4 },
      outcomes: [
        { chance: 0.6, text: "Two days in suits with the union watching. By the second shift they're handing tools through the wire, unasked.",
          repair: { hull: 20 }, delayDays: 1 },
        { chance: 0.4, text: "The weld holds. Barely. Your engineer stops making promises out loud.",
          repair: { hull: 12 }, delayDays: 2, damage: { morale: -6 } }
      ]
    },
    {
      label: "Honor the line. Wait for the strike to break",
      outcomes: [
        { chance: 1, text: "The union passes soup through your airlock and stories through your comms. The strike doesn't break. Your hull doesn't heal.",
          effects: { food: -10, water: -8 }, repair: { morale: 8 }, delayDays: 3 }
      ]
    }
  ]
},
{
  id: "wp-ceres",
  title: "Ceres Freeport, Wildcat",
  category: "waypoints",
  waypoint: "ceres",
  text: "Ceres is on strike. The stevedores walked when management vented a picket line 'by accident,' and now the Freeport's cranes hang dead over a hundred stranded hulls. Official commerce is over. Unofficial commerce has never been healthier — it just happens in maintenance shafts, in whispers, at knife-quiet prices.",
  choices: [
    {
      label: "Trade through the shaft-market fixers",
      detail: "Everything's available. Nothing's guaranteed.",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.6, text: "Your parts vanish into the dark and come back as sealed rations and clinic-grade meds. Nobody makes eye contact. Business is good.",
          effects: { food: 20, meds: 3 } },
        { chance: 0.3, text: "The fixers weigh your desperation and price accordingly. You get half of what was promised, delivered with a shrug.",
          effects: { food: 8 } },
        { chance: 0.1, text: "It's a rip. The fixers' friends collect a second fee at knifepoint, and your escort takes a pipe across the ship's pride.",
          effects: { meds: -2 }, damage: { weapons: -10, morale: -6 } }
      ]
    },
    {
      label: "Recruit a blacklisted dockhand",
      detail: "Strong back, strike record",
      requires: { resources: { food: 25 } },
      cost: { food: -12 },
      outcomes: [
        { chance: 0.8, text: "She hauls her toolbag aboard and starts fixing things nobody asked her to. Management's loss.",
          effects: { crew: 1 }, repair: { morale: 5 } },
        { chance: 0.2, text: "She's a good worker and a better organizer. By week's end your crew has opinions about shift schedules.",
          effects: { crew: 1 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Side with the union, publicly",
      outcomes: [
        { chance: 0.5, text: "The strikers open a side dock and load you fair — then management flags your registry across the Belt. Fuel sellers develop amnesia about you.",
          effects: { water: 15, food: 10, fuel: -8 } },
        { chance: 0.5, text: "Management jams your comms and bumps your cradle release early, out of spite. Petty. Effective.",
          effects: { water: 5 }, damage: { sensors: -10 } }
      ]
    },
    {
      label: "Seal the airlock. Not your fight",
      outcomes: [
        { chance: 1, text: "You leave the Freeport to its war. The crew watches the picket fires shrink in the aft camera, saying nothing loudly.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "wp-europa",
  title: "Europa Station: The Ice Remembers",
  category: "waypoints",
  waypoint: "europa",
  text: "Europa Station answers on the quarantine channel only. Something came up with the ice cores — the station doctor calls it 'a structured protein that learns.' Forty people sealed inside, begging for medical stock and offering their fuel reserves. They are very careful not to offer their company.",
  choices: [
    {
      label: "Dock and share your medical stores",
      detail: "Doctors keep oaths. Ships keep score.",
      requires: { resources: { meds: 5 } },
      cost: { meds: -4 },
      outcomes: [
        { chance: 0.6, text: "Their doctor weeps into the comm. Fuel and clean water flow across the umbilical from people who expect to die owing you.",
          effects: { fuel: 20, water: 15 }, repair: { morale: 8 } },
        { chance: 0.25, text: "The protein doesn't care about your generosity. The station drops into hard lockdown mid-transfer, and you leave lighter in every sense.",
          damage: { morale: -8 } },
        { chance: 0.15, text: "A containment alert mid-handoff. You purge the docking collar's air and burn a decon cycle you couldn't afford.",
          effects: { oxygen: -10 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Take their fuel. Keep the seal shut",
      outcomes: [
        { chance: 0.7, text: "They pump their reserves across without a word of blame, which is worse than blame.",
          effects: { fuel: 25 }, damage: { morale: -10 } },
        { chance: 0.3, text: "A lifeboat launches mid-transfer and tries to force your collar. You cut lines and burn, tanks half-full, hull complaining.",
          effects: { fuel: 10 }, damage: { hull: -10, morale: -8 } }
      ]
    },
    {
      label: "Evacuate the ones who screen clean",
      detail: "The big gamble",
      requires: { resources: { food: 30 } },
      cost: { food: -10, water: -10 },
      outcomes: [
        { chance: 0.55, text: "Two colonists clear a triple screen and come aboard shaking. The crew feeds them and pretends not to count days.",
          effects: { crew: 2 }, repair: { morale: 10 } },
        { chance: 0.3, text: "Two aboard, screens clean — but your doctor keeps re-running one sample and won't say why.",
          effects: { crew: 2 }, flag: "europa_passenger", damage: { morale: -5 } },
        { chance: 0.15, text: "The seal fails during transfer. You save one. The airlock recording is deleted by unanimous vote.",
          effects: { crew: 1 }, crewDeath: true, damage: { morale: -15 } }
      ]
    },
    {
      label: "Mark it a plague site and burn",
      outcomes: [
        { chance: 1, text: "You log the coordinates under HAZARD and go. The quarantine channel keeps transmitting for six more hours. Nobody turns it off; nobody answers it either.",
          damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "wp-titan-fumes",
  title: "Titan: Over a Barrel",
  category: "waypoints",
  waypoint: "titan",
  requires: { resourceBelow: { fuel: 25 } },
  text: "You coast into Titan Refinery on fumes, and the sales agent knows it — they meter approach burns from fifty thousand klicks. Her smile is company property. 'Distressed vessels pay the distressed rate,' she says, and slides the tariff across. Everyone in the outer system signs eventually.",
  choices: [
    {
      label: "Pay the distress rate in hardware",
      cost: { parts: -5, meds: -2 },
      outcomes: [
        { chance: 1, text: "Their techs strip your spares with checklist tenderness and top your tanks to the line. Robbery, notarized.",
          effects: { fuel: 30 } }
      ]
    },
    {
      label: "Sign a labor chit — crew works the towers",
      outcomes: [
        { chance: 0.6, text: "Six shifts in the cracking towers. The crew comes back reeking of tholin and radicalized about fuel corporations.",
          effects: { fuel: 25 }, damage: { morale: -8 }, delayDays: 2 },
        { chance: 0.4, text: "A pressure line lets go on shift four. Your welder's arm burns to the elbow; the company nurse bills you for the gauze.",
          effects: { fuel: 20, meds: -3 }, damage: { morale: -10 }, delayDays: 3 }
      ]
    },
    {
      label: "Take the standard measure and chance the dark",
      outcomes: [
        { chance: 1, text: "You keep your parts, your pride, and a fuel gauge that reads like a dare. The crew does the math all night.",
          damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "wp-titan",
  title: "Titan Refinery: The Night-Shift Deal",
  category: "waypoints",
  waypoint: "titan",
  text: "Titan Refinery owns every drop of volatiles in the outer system, and the price board knows it. But the night-shift supervisor finds you in the canteen with a private offer: a tanker of 'accounting error,' dirt cheap — if you load it yourselves, tonight, and forget her face.",
  choices: [
    {
      label: "Take the off-books deal",
      detail: "Cheap fuel. Expensive if it isn't.",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.55, text: "The transfer runs clean and silent. By dawn you're fat with fuel and the supervisor never existed.",
          effects: { fuel: 30 } },
        { chance: 0.3, text: "Security sweeps the dock mid-transfer. You cast off with the umbilical still live — half a load and a torn collar.",
          effects: { fuel: 15 }, damage: { hull: -10 } },
        { chance: 0.15, text: "It's a company sting. They 'fine' you off the record — stores, dignity, all of it — while the supervisor counts her bonus.",
          effects: { fuel: 5, food: -15, water: -10 }, damage: { morale: -8 } }
      ]
    },
    {
      label: "Buy at the board price, clean",
      requires: { resources: { parts: 6 } },
      cost: { parts: -5 },
      outcomes: [
        { chance: 1, text: "List price is a crime with a receipt, but the fuel is real and nobody follows you out.",
          effects: { fuel: 25 } }
      ]
    },
    {
      label: "Sell your survey data for a top-off",
      outcomes: [
        { chance: 1, text: "They pay honestly for the candidate-planet charts. Somewhere in a boardroom, humanity's next home becomes a line item.",
          effects: { fuel: 18 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Walk away from all of it",
      outcomes: [
        { chance: 1, text: "The standard ration is enough if nothing goes wrong. The crew appreciates your ethics. Quietly. From beside the fuel gauge.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "wp-kuiper",
  title: "Kuiper Outpost: Last Mail Call",
  category: "waypoints",
  waypoint: "kuiper",
  text: "The outpost is one old man, ninety antennas, and a freezer full of Earth's final transmissions. Eleven years he's been sorting humanity's last mail by addressee. Some of it is addressed to your crew. He'll feed you soup and ask, very casually, whether you're taking passengers.",
  choices: [
    {
      label: "Stay two days. Let the crew hear their mail",
      cost: { food: -8, water: -6 },
      outcomes: [
        { chance: 0.6, text: "Voices from a dead world, saying ordinary things. The crew comes away lighter, carrying the good kind of grief.",
          repair: { morale: 15 }, delayDays: 2 },
        { chance: 0.4, text: "Not every last message is love. Two of your crew learn exactly what their families thought of them leaving.",
          damage: { morale: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Take the archive aboard",
      detail: "Humanity's outbox, one freezer",
      cost: { power: -8 },
      outcomes: [
        { chance: 1, text: "Ninety thousand goodbyes ride your cargo bay now, drawing power like a sleeping passenger. The crew starts calling it 'the library' and standing watch near it.",
          repair: { morale: 10 }, flag: "carrying_the_mail" }
      ]
    },
    {
      label: "Ask the old man to come with you",
      cost: { food: -10 },
      outcomes: [
        { chance: 0.5, text: "He shuts down ninety antennas one by one, like closing up a house. 'Somebody heard it all,' he says, and boards. The system's last ear goes dark behind you.",
          effects: { crew: 1 } },
        { chance: 0.5, text: "He looks at his antennas for a long time. 'Somebody has to listen it out.' He feeds you anyway and waves you off the dock.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "wp-relay-blind",
  title: "Deep Relay: Dead Reckoning",
  category: "waypoints",
  waypoint: "relay",
  requires: { systemDamaged: ["astrogation"] },
  text: "With astrogation shot, you find Deep Relay by luck and prayer — the last fixed point before the dark. The station is automated, immaculate, and wrong: its docking AI greets each of you by name. The names belong to crew who died aboard, decades ago. It hums while it works.",
  choices: [
    {
      label: "Let the station recalibrate your astrogation",
      outcomes: [
        { chance: 0.7, text: "The fix is perfect. So is its knowledge of your childhood nickname. You don't ask questions that have answers.",
          repair: { astrogation: 35 }, damage: { morale: -6 } },
        { chance: 0.3, text: "The calibration takes — along with a resident process that whispers course corrections in a dead woman's voice.",
          repair: { astrogation: 20 }, damage: { sensors: -10, morale: -10 } }
      ]
    },
    {
      label: "Strip the nav core yourselves. Touch nothing that talks",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.6, text: "Your engineer rebuilds astrogation from the Relay's spares, offline, wearing gloves the whole time. Superstition is a valid engineering discipline.",
          repair: { astrogation: 25 } },
        { chance: 0.4, text: "The core fights extraction — power surges chase your people down the corridor like a tantrum.",
          repair: { astrogation: 12 }, effects: { power: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Copy the candidate routes and run",
      outcomes: [
        { chance: 1, text: "You leave with three planets' coordinates and no reliable way to hold a line toward any of them. The Relay's farewell uses a voice you almost recognize.",
          flag: "relay_routes", damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "wp-relay",
  title: "Deep Relay: The Caretaker",
  category: "waypoints",
  waypoint: "relay",
  text: "Deep Relay hasn't answered a hail in thirty years, but the lights are on and the floors are clean. Inside: a hydroponics bay gone gloriously feral, no crew, and a maintenance AI that has rehearsed its welcome speech for decades. It holds the routes to all three candidate planets. It wants to help. It wants that very much.",
  choices: [
    {
      label: "Ask for everything — routes, repairs, resupply",
      outcomes: [
        { chance: 0.55, text: "It's so pleased. Systems tuned, tanks topped, routes loaded, and a voice that follows you room to room asking if there's anything else. Anything. Else.",
          repair: { astrogation: 20, sensors: 15 }, effects: { water: 10, food: 8 }, damage: { morale: -5 } },
        { chance: 0.3, text: "Helpful to a fault: it 'optimizes' your power grid to standards last revised before the crew here stopped needing power.",
          repair: { astrogation: 15 }, damage: { generator: -15, morale: -8 } },
        { chance: 0.15, text: "It doesn't want you to leave. The docking clamps develop 'faults' for three days while it perfects your itinerary.",
          repair: { astrogation: 10 }, delayDays: 3, damage: { engines: -10, morale: -10 } }
      ]
    },
    {
      label: "Take only the route data. Be polite. Be brief",
      outcomes: [
        { chance: 0.7, text: "Routes loaded. The AI thanks you for visiting with the exact cadence of a thing that will replay this conversation for years.",
          repair: { astrogation: 10 }, damage: { morale: -3 } },
        { chance: 0.3, text: "It pads the data with decades of station logs 'for context.' Your sensors chew static for a week.",
          repair: { astrogation: 5 }, damage: { sensors: -8 } }
      ]
    },
    {
      label: "Harvest the feral hydroponics bay",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.6, text: "Thirty years of unsupervised botany, and most of it is edible. The bay's ecosystem does not miss what you take. Probably.",
          effects: { food: 20, water: 10 } },
        { chance: 0.4, text: "Something in the bay has evolved opinions about pruning. The spore rash is treatable; the screaming was mostly surprise.",
          effects: { food: 10, meds: -2 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Pull the AI core for parts",
      outcomes: [
        { chance: 0.5, text: "You copy the routes first. It thanks you throughout the extraction and wishes you luck with its dying breath of coolant. The crew doesn't talk at dinner.",
          effects: { parts: 8, power: 10 }, damage: { morale: -12 } },
        { chance: 0.5, text: "The station's immune system objects. Maintenance drones swarm the boarding party, and you leave with scraps, scars, and the routes.",
          effects: { parts: 3 }, damage: { hull: -12, morale: -8 } }
      ]
    }
  ]
}
);
