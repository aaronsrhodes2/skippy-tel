/* ORION TRAIL — traders & travelers. Prefix: trd- */
SCENARIOS.push(
{
  id: "trd-001",
  title: "The Tinker Barge",
  category: "traders",
  text: "A patchwork barge flying forty flags of dead nations matches your vector. The captain grins across the comm link with more gold teeth than cargo manifest. 'Friend! Everything is for sale. Especially the things that aren't.'",
  once: false,
  weight: 1.2,
  choices: [
    {
      label: "Trade food for parts",
      requires: { resources: { food: 20 } },
      cost: { food: -15 },
      outcomes: [
        { chance: 0.85, text: "The parts are mismatched, sun-faded, and genuine. Chen sorts them like a dragon with a hoard.",
          effects: { parts: 4 } },
        { chance: 0.15, text: "Half the crate is painted scrap. By the time you're sure, the barge is a dot. The gold teeth make sense now.",
          effects: { parts: 1 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Trade fuel for meds",
      requires: { resources: { fuel: 20 } },
      cost: { fuel: -12 },
      outcomes: [
        { chance: 0.85, text: "Sealed med kits, dated but honest. The doctor blesses the barge captain and all forty flags.",
          effects: { meds: 4 } },
        { chance: 0.15, text: "The 'broad-spectrum antibiotics' are vitamin C. Effective against scurvy and nothing else you're afraid of.",
          effects: { meds: 1 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Wave and keep flying",
      outcomes: [
        { chance: 1, text: "The barge dips its running lights in farewell. You will wonder about the crate marked GENUINE EARTH SOIL for weeks.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "trd-002",
  title: "The Water Baron",
  category: "traders",
  text: "The Hyacinth is a tanker the size of a small moon, hull painted with a smiling blue droplet. Baron Okafor owns every honest liter between here and Titan and prices it accordingly. 'Thirst,' he says warmly, 'is the only currency that never inflates.'",
  once: false,
  weight: 1.0,
  choices: [
    {
      label: "Pay fuel for water",
      requires: { resources: { fuel: 20 } },
      cost: { fuel: -14 },
      outcomes: [
        { chance: 0.85, text: "Clean water, metered to the milliliter. The Baron's monopoly is many things. Sloppy is not one of them.",
          effects: { water: 20 } },
        { chance: 0.15, text: "The transfer meter runs slow. His meter, his math, his gunships watching politely from a distance.",
          effects: { water: 9 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Pay parts for water",
      requires: { resources: { parts: 4 } },
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.85, text: "He inspects each part like a jeweler, then fills your tanks. Fair, in the way a tollbooth is fair.",
          effects: { water: 18 } },
        { chance: 0.15, text: "'Depreciation,' he says, docking you a third for wear. The water still tastes like surrender.",
          effects: { water: 8 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Decline and ration harder",
      outcomes: [
        { chance: 1, text: "The tanker recedes, droplet smiling. Someone recalculates the water ration out loud. Nobody thanks them for the arithmetic.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "trd-003",
  title: "The Karst Brothers",
  category: "traders",
  text: "Three matched cutters in fresh grey paint. The Karst brothers 'found' a naval depot six months back, and now they are the parts economy out here. Military-grade everything, prices to match, smiles with the serial numbers filed off.",
  once: false,
  weight: 0.8,
  choices: [
    {
      label: "Trade food for parts",
      requires: { resources: { food: 25 } },
      cost: { food: -18 },
      outcomes: [
        { chance: 0.85, text: "Vacuum-sealed naval spares, tolerances tighter than anything you have touched since Earth. Worth every calorie. Barely.",
          effects: { parts: 5 } },
        { chance: 0.15, text: "The crate is padded generously with foam and lies. Two real parts, artfully arranged. The brothers are already burning.",
          effects: { parts: 2 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Trade meds for parts",
      requires: { resources: { meds: 3 } },
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.85, text: "They take the meds without haggling, which tells you something about life in the cartel. Good parts, though.",
          effects: { parts: 4 } },
        { chance: 0.15, text: "Bottom of the crate: depot rejects, condemned tags still wired on. The doctor wants her painkillers back.",
          effects: { parts: 1 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Keep your distance",
      outcomes: [
        { chance: 1, text: "Three cutters, one vector, no pursuit. Chen mourns the torque wrenches aloud for two days.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "trd-004",
  title: "The Apothecary",
  category: "traders",
  text: "A sleek black yacht, no transponder, a caduceus etched under the airlock. Doctor Voss smuggles real pharmaceuticals — sealed, dated, cold-chained the whole way. She is honest to the decimal point, and the decimal point is brutal.",
  choices: [
    {
      label: "Buy the full pharmacy",
      detail: "Real medicine, robbery pricing",
      requires: { resources: { fuel: 30 } },
      cost: { fuel: -25 },
      outcomes: [
        { chance: 1, text: "Every seal intact, every date good. You paid triple what Earth would have charged, but Earth is dead and Voss is not.",
          effects: { meds: 8 } }
      ]
    },
    {
      label: "Buy a field kit",
      requires: { resources: { food: 30 } },
      cost: { food: -22 },
      outcomes: [
        { chance: 1, text: "A compact kit, surgical grade. Voss counts your rations twice and gives nothing away free, including sympathy.",
          effects: { meds: 5 } }
      ]
    },
    {
      label: "Walk away from the prices",
      outcomes: [
        { chance: 1, text: "She does not haggle. The yacht is gone before you finish saying 'maybe.' The doctor stares at the empty med locker.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "trd-005",
  title: "The Long Caravan",
  category: "traders",
  text: "Nine ships strung nose to tail like beads: the Delacroix-Okonkwo caravan, four generations deep. They do not sell things so much as evenings. Dinner, music, a bunk that does not smell like your ship. Payment expected. Hospitality genuine.",
  once: false,
  weight: 0.7,
  choices: [
    {
      label: "Buy the crew a night aboard",
      detail: "Costs food and a day",
      requires: { resources: { food: 15 } },
      cost: { food: -10 },
      outcomes: [
        { chance: 0.8, text: "Real cooking, borrowed children underfoot, an accordion that has survived two solar systems. The crew comes back human.",
          repair: { morale: 15 }, delayDays: 1 },
        { chance: 0.2, text: "A fine evening, until your engineer loses spare fittings in a card game the grandmother has been winning since Earth.",
          effects: { parts: -2 }, repair: { morale: 10 }, delayDays: 1 }
      ]
    },
    {
      label: "Trade water for news and oddments",
      requires: { resources: { water: 15 } },
      cost: { water: -10 },
      outcomes: [
        { chance: 0.8, text: "The grandmother knows every ship on the route and half their secrets. She throws in preserves and a coil of wire.",
          effects: { food: 6, parts: 2 } },
        { chance: 0.2, text: "The news is mostly obituaries of people you never met. The preserves are good. The silence afterward is not.",
          effects: { food: 4 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Keep flying",
      outcomes: [
        { chance: 1, text: "The music carries on the open comm band for an hour after. Nobody in the galley says anything at all.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "trd-006",
  title: "The Skimmer",
  category: "traders",
  text: "A scorched scoopship hangs at the edge of the gravity well, tanks bulging with skimmed hydrogen. The pilot calls herself Magpie and calls her fuel 'mostly what it says on the tank.' The price is too good. So, probably, is the story.",
  once: false,
  weight: 0.6,
  zones: ["outer"],
  choices: [
    {
      label: "Trade parts for cheap fuel",
      requires: { resources: { parts: 4 } },
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.6, text: "The hydrogen is clean. Magpie salutes with two fingers and drops back toward the giant like a stone that changed its mind.",
          effects: { fuel: 20 } },
        { chance: 0.4, text: "The mix is cut with something inert and unapologetic. The engines cough it down like bad medicine.",
          effects: { fuel: 8 }, damage: { engines: -10 } }
      ]
    },
    {
      label: "Trade food for cheap fuel",
      requires: { resources: { food: 20 } },
      cost: { food: -14 },
      outcomes: [
        { chance: 0.6, text: "Good fuel, and she eats a ration bar during the transfer like it is a religious experience. Been out here a while, then.",
          effects: { fuel: 18 } },
        { chance: 0.4, text: "Half the transfer is boil-off and optimism. The injectors file a formal complaint in the language of grinding.",
          effects: { fuel: 7 }, damage: { engines: -10 } }
      ]
    },
    {
      label: "Too good to be true",
      outcomes: [
        { chance: 1, text: "Magpie shrugs, a whole-body gesture visible across two kilometers of vacuum. The fuel gauge does not shrug back.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "trd-007",
  title: "The Fine Print",
  category: "traders",
  text: "The Fine Print is one woman and forty terabytes. Ledger sells survey data, debris tracks, good burn windows. 'Everyone else sells you things,' she says. 'I sell you knowing.' Her rates suggest knowing is the scarcest cargo left flying.",
  choices: [
    {
      label: "Buy current route charts",
      detail: "Recalibrates astrogation",
      requires: { resources: { food: 18 } },
      cost: { food: -12 },
      outcomes: [
        { chance: 0.85, text: "The charts are current to within a month. Your astrogation plot tightens like a sentence losing adjectives.",
          repair: { astrogation: 30 } },
        { chance: 0.15, text: "Current, she said. Current five years ago, the metadata says. Some corrections still hold. Some.",
          repair: { astrogation: 10 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Buy sensor calibration profiles",
      requires: { resources: { parts: 3 } },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.85, text: "The profiles snap your sensor returns into focus. Space is still trying to kill you, but now you can watch it try.",
          repair: { sensors: 30 } },
        { chance: 0.15, text: "The profiles are for a different sensor package. Close enough to help a little. Close enough to sting, too.",
          repair: { sensors: 10 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Trust your own instruments",
      outcomes: [
        { chance: 1, text: "Ledger logs your refusal — literally, you watch her type it. 'For the actuaries,' she says, and closes the channel.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "trd-008",
  title: "The Traveling Garden",
  category: "traders",
  text: "The Svalbard II is a greenhouse bolted to an engine, every window green. Curator Ngo guards forty thousand species and trades cuttings like a miser paying out heirlooms. 'Earth is dead,' she agrees. 'Its plants are merely traveling.'",
  choices: [
    {
      label: "Trade water for sprouting trays",
      requires: { resources: { water: 20 } },
      cost: { water: -15 },
      outcomes: [
        { chance: 0.85, text: "Fast greens, engineered for ships. In ten days the galley smells like something other than regret.",
          effects: { food: 16 } },
        { chance: 0.15, text: "Half the trays damp off in your recycled air. Ngo warned you about the humidity. You remember that now.",
          effects: { food: 6 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Trade meds for preserved stores",
      requires: { resources: { meds: 3 } },
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.85, text: "Dried fruit from trees that no longer exist anywhere else. The crew eats history in small, careful bites.",
          effects: { food: 14 } },
        { chance: 0.15, text: "The cases underneath are seed stock, not food — precious to civilization, inedible to you. Ngo calls it an honest mistake.",
          effects: { food: 5 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Just look at the green awhile",
      outcomes: [
        { chance: 1, text: "She lets you float at the viewport for ten minutes, free of charge. Leaving costs more than you expected.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "trd-009",
  title: "The Ferryman",
  category: "traders",
  text: "A black ship towing a mile of tethered coffins, each one catching starlight. The Ferryman's crew trades in remembrance: they archive your dead, your stories, your Earth, and they pay in goods. 'Memory is cargo,' the mate says. 'We just carry it further.'",
  choices: [
    {
      label: "Sell your memories of Earth",
      detail: "The telling costs something",
      outcomes: [
        { chance: 0.7, text: "Hours of recording. Oceans, rain, a specific dog. The archive pays well, and the telling takes something out of everyone.",
          effects: { food: 8, meds: 2 }, damage: { morale: -10 } },
        { chance: 0.3, text: "Halfway through, the grief turns to laughter — the good stories surface with the sad ones. The mate pays and thanks you.",
          effects: { food: 8, meds: 2 }, repair: { morale: 8 } }
      ]
    },
    {
      label: "Sell the ship's log archive",
      outcomes: [
        { chance: 1, text: "You hand over the raw logs. Their archivists are thorough. Your backups, it turns out, were less so — nav history has gaps now.",
          effects: { parts: 3, fuel: 5 }, damage: { astrogation: -10 } }
      ]
    },
    {
      label: "Let the dead pass",
      outcomes: [
        { chance: 1, text: "The coffins glitter astern for hours. Everyone finds a viewport-facing chore that needs doing slowly.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "trd-010",
  title: "The Order of the Torque",
  category: "traders",
  text: "A monastery ship, engines humming a perfect fifth. The Order of the Torque takes vows of maintenance. For a donation, Brother Anchor will lay hands on your drive. His hands hold a calibrated wrench and no illusions about your maintenance schedule.",
  choices: [
    {
      label: "Full engine sacrament",
      detail: "Parts and food, real work",
      requires: { resources: { parts: 5, food: 15 } },
      cost: { parts: -4, food: -8 },
      outcomes: [
        { chance: 0.9, text: "Twelve hours of chanted torque specs. The drive purrs afterward like it has been forgiven for something.",
          repair: { engines: 40 } },
        { chance: 0.1, text: "Brother Anchor finds something worse underneath and fixes what he can, tutting in Gregorian. The rest will need a shipyard.",
          repair: { engines: 15 }, delayDays: 1 }
      ]
    },
    {
      label: "The quick blessing",
      requires: { resources: { parts: 2 } },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.85, text: "An hour of inspection, three tightened fittings, one disapproving look at your coolant log. It helps.",
          repair: { engines: 15 } },
        { chance: 0.15, text: "He adjusts what he can reach and leaves a pamphlet titled ENTROPY AND YOU. The engines are marginally consoled.",
          repair: { engines: 5 } }
      ]
    },
    {
      label: "Decline the sacrament",
      outcomes: [
        { chance: 1, text: "The monastery ship departs in perfect harmonic tune. The crew listens to your own engines' arrhythmia with newly educated ears.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "trd-011",
  title: "The Providence",
  category: "traders",
  text: "A gleaming liner matches vectors and simply gives: crates of food, cells of fuel, no price named. The captain waves off thanks. 'Out here, we take care of each other.' Nobody out here has ever taken care of anybody for free.",
  choices: [
    {
      label: "Accept the gifts",
      detail: "A day for cargo transfer",
      outcomes: [
        { chance: 1, text: "The crates check out — real food, real fuel. Their manifest scanner pings your hull registry twice on the way out. Probably nothing.",
          effects: { food: 15, fuel: 10 }, delayDays: 1, flag: "trd_providence_debt" }
      ]
    },
    {
      label: "Refuse politely",
      outcomes: [
        { chance: 1, text: "You wave off the crates. The crew stares at you like you shot a holiday. The captain's smile widens, which is somehow worse.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "trd-012",
  title: "The Collection",
  category: "traders",
  weight: 1.3,
  requires: { requiresFlag: "trd_providence_debt" },
  sensorWarning: "Two contacts on intercept. Matched livery. Weapons warm.",
  text: "Two gunships in the Providence's livery drop off your bow. The bill has arrived, itemized, with interest. 'Charity,' the collector explains, 'is a loan with better marketing.' Your hull registry is on their screen. Pay, or be paid from.",
  choices: [
    {
      label: "Pay the bill",
      detail: "More than they gave. Obviously.",
      requires: { resources: { food: 20, fuel: 15 } },
      cost: { food: -20, fuel: -15 },
      outcomes: [
        { chance: 1, text: "They take it with a receipt and a customer satisfaction survey. The crew learns what the gifts cost. So do you.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Refuse and run",
      outcomes: [
        { chance: 0.5, text: "You burn hard and lose them in a debris ring, engines screaming the whole way. Expensive. Cheaper than the bill.",
          effects: { fuel: -10 }, damage: { engines: -10 } },
        { chance: 0.3, text: "They hole the cargo section before you clear their range. Supplies vent, glittering, into the collector's ledger.",
          effects: { food: -10 }, damage: { hull: -20 } },
        { chance: 0.2, text: "A parting shot finds the drive. You escape at a limp, doing debt-collection math in delta-v.",
          damage: { engines: -25 }, delayDays: 2 }
      ]
    },
    {
      label: "Fight",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.6, text: "Gunships built for scaring farmers meet a ship with nothing left to lose. They scatter. You pick a sensor pod out of the wreckage.",
          effects: { parts: 3 }, damage: { hull: -10 } },
        { chance: 0.4, text: "They shoot back better than expected. You drive them off, eventually, at a price the Providence would call fair.",
          damage: { hull: -25, weapons: -15 } }
      ]
    }
  ]
}
);
