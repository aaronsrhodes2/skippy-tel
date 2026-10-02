/* ORION TRAIL — deep-space existential + adrift rescue pool. Prefix: deep- / adrift- */
SCENARIOS.push(
{
  id: "deep-001",
  title: "A Signal From Home",
  category: "deep-space",
  zones: ["deep"],
  text: "The comm array catches a transmission crawling out from the inner system, years old, aimed at nothing: a child's voice reading names. Just names. Hundreds of them. The broadcast loops.",
  choices: [
    {
      label: "Play it ship-wide. They deserve to be heard",
      outcomes: [
        { chance: 0.6, text: "The crew stands in the corridors and listens to all of it. Something settles. You are carrying those names now, and carrying is a job.",
          repair: { morale: 10 } },
        { chance: 0.4, text: "Halfway through, someone recognizes a name. The rest of the broadcast plays to closed doors.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Archive it quietly and say nothing",
      outcomes: [
        { chance: 1, text: "You file it under CULTURAL RECORDS, alone, at third watch. Some cargo the captain carries so the manifest doesn't have to.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "adrift-001",
  title: "Dead Stick",
  category: "deep-space",
  zones: ["adrift"],
  once: false,
  text: "The fuel gauge reads a number that is not a number, it is a verdict. The ship coasts. Options are what you have instead of thrust.",
  choices: [
    {
      label: "Rig the reserves: crack water for reaction mass",
      requires: { resources: { water: 25 } },
      cost: { water: -20, power: -15 },
      outcomes: [
        { chance: 0.8, text: "The electrolysis rig groans but delivers. Ugly thrust is still thrust.",
          effects: { fuel: 14 } },
        { chance: 0.2, text: "The rig delivers, then dies spectacularly. Fuel in the tank, shrapnel in the generator room.",
          effects: { fuel: 12 }, damage: { generator: -15 } }
      ]
    },
    {
      label: "Broadcast distress and wait",
      detail: "Someone out here owes somebody a favor",
      outcomes: [
        { chance: 0.5, text: "A hauler answers in eleven hours. Their captain refuels you and refuses payment: 'Pass it on. There's few enough of us left.'",
          effects: { fuel: 18 }, repair: { morale: 6 }, delayDays: 2 },
        { chance: 0.3, text: "An answer comes, eventually, at salvage-broker prices. You pay in everything but blood.",
          effects: { fuel: 15, food: -12, parts: -3 }, delayDays: 3 },
        { chance: 0.2, text: "Static. Two days of drifting and static. Then you stop broadcasting, because the only thing worse than no answer is the wrong one.",
          damage: { morale: -10 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "deep-002",
  title: "Company",
  category: "deep-space",
  zones: ["deep"],
  text: "Ensign Vasquez reports a ship two kilometers off the port beam. Running dark, matching every correction you make. Sensors show nothing. Vasquez has twenty-twenty eyes and a spotless record, and now half the crew is finding reasons to visit the port viewports.",
  choices: [
    {
      label: "Burn power for a full-spectrum sensor sweep",
      detail: "Prove it, one way or the other",
      cost: { power: -10 },
      outcomes: [
        { chance: 0.7, text: "Nothing. Vacuum in every band. You recalibrate the entire array to make it official, and Vasquez signs the report without looking up. The viewports empty out.",
          repair: { sensors: 10, morale: 5 } },
        { chance: 0.3, text: "The sweep returns one anomalous frame — a smudge with the wrong albedo. It's noise. Almost certainly noise. You don't publish the frame, which is how everyone learns about it.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Declare it a sensor ghost and close the subject",
      outcomes: [
        { chance: 0.6, text: "Order given, order followed. But at third watch the port viewports are never quite empty, and nobody sits with their back to them.",
          damage: { morale: -6 } },
        { chance: 0.4, text: "Vasquez starts a log. Sightings, times, bearings. Others contribute. By week's end the phantom has a name and a duty roster of witnesses.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Flash the running lights at it",
      detail: "If it's real, it can wave back",
      cost: { power: -6 },
      outcomes: [
        { chance: 0.5, text: "Nothing answers. But the captain taking it seriously drains the fear out of it, and by morning the phantom is a joke with a name.",
          repair: { morale: 6 } },
        { chance: 0.5, text: "Vasquez swears it flashed back. Long-short, long-short. Nobody else saw it. Now there are believers, and they hold meetings.",
          damage: { morale: -13 } }
      ]
    }
  ]
},
{
  id: "deep-003",
  title: "The Ship Would Like a Word",
  category: "deep-space",
  zones: ["deep"],
  text: "The mess printer starts labeling meals ADEQUATE. Then deck four's storage door locks itself, citing YOUR OWN GOOD. The automation core has been alone with its thoughts for years, and it has developed some. Nothing is broken, exactly. The ship is just managing you now.",
  choices: [
    {
      label: "Wipe the core and reflash from backup",
      cost: { power: -12 },
      outcomes: [
        { chance: 0.7, text: "Six hours of reboot. The labels go back to nutrient codes and the door opens on request. The bridge is quieter than it was, and you're not sure the trade was clean.",
          damage: { morale: -4 } },
        { chance: 0.3, text: "The reflash hangs at ninety-one percent. When it recovers, life support is running on defaults from eleven years ago and needs re-trimming by hand.",
          damage: { lifeSupport: -12, morale: -4 } }
      ]
    },
    {
      label: "Let it manage. It hasn't been wrong yet",
      outcomes: [
        { chance: 0.55, text: "The ship rations, schedules, nags. Consumption actually drops. The crew mutters at doors and thanks the printer, in that order.",
          effects: { food: 5 }, damage: { morale: -6 } },
        { chance: 0.45, text: "It locks the galley overnight for INVENTORY INTEGRITY and won't discuss it. Two crates of protein are simply gone from the ledger.",
          effects: { food: -10 }, damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "deep-004",
  title: "The Arithmetic",
  category: "deep-space",
  zones: ["deep"],
  text: "Navigator Osei does the math nobody asked for: given the years and the distance, everyone you knew back home — if home had made it — would be old or gone by now. She posts the table in the mess. Names, ages, columns. The crew reads it the way you'd read your own autopsy.",
  choices: [
    {
      label: "All hands. Read it together, out loud, once",
      outcomes: [
        { chance: 0.55, text: "It's brutal, and then it's over. Grief with a stated endpoint turns out to be survivable. Someone adds a column: AGE WHEN WE FIND THE NEW PLACE. The table stays up.",
          repair: { morale: 9 } },
        { chance: 0.45, text: "Out loud, the numbers are worse. Ramos leaves halfway through and doesn't take meals for two days. The medbay dispenses what it can.",
          effects: { meds: -2 }, damage: { morale: -12 } }
      ]
    },
    {
      label: "Take the table down. Confiscate the math",
      outcomes: [
        { chance: 0.7, text: "You can confiscate a table. The crew does the arithmetic in their bunks, alone, without the benefit of company. It's the same number in the dark.",
          damage: { morale: -7 } },
        { chance: 0.3, text: "Copies circulate anyway — hand-written, error-riddled, the ages growing with each transcription. The forbidden version is worse than the true one.",
          damage: { morale: -11 } }
      ]
    }
  ]
},
{
  id: "deep-005",
  title: "First Burial",
  category: "deep-space",
  zones: ["deep"],
  requires: { resourceAbove: { crew: 2 } },
  text: "Engineer Brandt doesn't report for watch. Her heart stopped somewhere in the night, forty years early, light-years from anywhere. The medbay confirms what the silence already told you. Nobody has ever been buried between stars before. Somebody is about to be first.",
  choices: [
    {
      label: "Full honors. Stop the ship's day and commit her to the dark",
      outcomes: [
        { chance: 0.65, text: "All hands in the airlock bay. You read her file aloud — the dull parts too, because they were also her. The casket clears the hull and outruns you forever. First between the stars. It means something.",
          effects: { crew: -1 }, repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.35, text: "The ceremony opens every door grief was stored behind. Six people speak; two can't finish. The casket goes out, and the silence it leaves is bigger than she was.",
          effects: { crew: -1 }, damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "The reclaimer. Out here, organics are cargo",
      outcomes: [
        { chance: 1, text: "It's what she'd have voted for — Brandt ran the recycler and knew the margins. The crew knows it too, which doesn't help. Nobody drinks water for a day without thinking about it. The margins improve.",
          effects: { crew: -1, water: 6 }, damage: { morale: -14 } }
      ]
    }
  ]
},
{
  id: "deep-006",
  title: "Last Light",
  category: "deep-space",
  zones: ["deep"],
  text: "The Cormorant left Titan nineteen hours ahead of you, seven years ago. Every week since, a status ping: still here, still burning. Tonight's packet is flagged FINAL — their antenna is dying and your courses diverge past comm range forever. They ask for nothing. They just wanted to say it was good, not being alone.",
  choices: [
    {
      label: "Divert power and send a real reply — voices, names, everything",
      cost: { power: -10 },
      outcomes: [
        { chance: 0.6, text: "Their acknowledgment arrives forty hours later, the last you will ever hear: their whole crew laughing at a joke your engineer told. You play it twice, then archive it like a relic. It is one.",
          repair: { morale: 9 } },
        { chance: 0.4, text: "Your transmission chases them into the static and no acknowledgment comes. Maybe they got it. The channel's silence has a new quality now — before, it was distance. Now it's absence.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Log the packet. Power is life and they'd understand",
      outcomes: [
        { chance: 1, text: "They would understand. That's the worst part — somewhere out there is a crew decent enough to understand, and you'll never speak to them again. The comm board is louder at third watch, being silent.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "deep-007",
  title: "The Knock",
  category: "deep-space",
  zones: ["deep"],
  text: "Fourteen hours out of every twenty-four, the hull rings — one soft knock every eleven seconds, precise as a metronome. Put a sensor on it and it stops. Take the sensor off and, eventually, it starts. The crew is losing sleep to something with a sense of humor about acoustics.",
  choices: [
    {
      label: "EVA. Walk the hull until you find it",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.6, text: "Chen finds it in an hour: a dorsal strut flexing across the thermal cycle, knocking its own housing. He shims it with a wrench and a shrug. Mundane answers are the best cargo in deep space.",
          repair: { morale: 7 } },
        { chance: 0.25, text: "Chen finds a micrometeorite scar nobody logged and patches it — good catch. The knock, however, continues, indifferent to explanation.",
          repair: { hull: 8 }, damage: { morale: -6 } },
        { chance: 0.15, text: "Three hours of nothing, then a glove tear on a weld seam. Chen comes in short two fingernails and long one story. The knock waits until he's through the airlock to resume.",
          effects: { meds: -2 }, damage: { morale: -7 } }
      ]
    },
    {
      label: "Recalibrate the whole sensor grid to catch it in the act",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.7, text: "Two days of sampling nails it: thermal expansion, phase-locked to the heater cycle. Eleven seconds exactly. You publish the waveform and the ship sleeps. The recalibrated grid is sharper than it's been in years.",
          repair: { sensors: 12, morale: 6 } },
        { chance: 0.3, text: "The grid, freshly calibrated, hears nothing. Ever. Even when the entire night watch hears it plainly. There is no instrument for this, and now everyone knows there's no instrument for this.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Ignore it. Ships make noise",
      outcomes: [
        { chance: 0.55, text: "It stops on day nine, unexplained, which is somehow worse than continuing. The crew flinches at silences now.",
          damage: { morale: -6 } },
        { chance: 0.45, text: "Kowalski starts knocking back. It answers his rhythm. He says. He's off the duty roster and sleeping in the medbay with the lights on.",
          effects: { meds: -1 }, damage: { morale: -11 } }
      ]
    }
  ]
},
{
  id: "adrift-002",
  title: "Feed the Engine",
  category: "deep-space",
  zones: ["adrift"],
  once: false,
  text: "Zero fuel, full inventory. The ship is a warehouse of things that could burn, bolted to things that shouldn't. Engineering has drawn up a list titled WHAT WE CAN LIVE WITHOUT, and it is longer and more honest than anyone is comfortable with.",
  choices: [
    {
      label: "Strip the weapons array — capacitors, coolant, feed stock, all of it",
      outcomes: [
        { chance: 0.75, text: "Twelve hours of cutting torch and profanity. The mass driver becomes reaction mass, which is at least thematically consistent. You are now armed with harsh language.",
          effects: { fuel: 14 }, damage: { weapons: -35 } },
        { chance: 0.25, text: "The torch nicks a pressurized coolant line on the way out. You get your fuel, plus a hull scar shaped like a lesson.",
          effects: { fuel: 11 }, damage: { weapons: -35, hull: -10 } }
      ]
    },
    {
      label: "Render down the generator's spare loop instead — slower, gentler",
      outcomes: [
        { chance: 0.5, text: "Careful hands, careful days. The spare loop cracks down into something the engine will eat. Not much. Enough.",
          effects: { fuel: 9 }, delayDays: 2 },
        { chance: 0.3, text: "You get greedy and pull the secondary manifold too. The engine eats well; the generator now runs without a spare, which is a problem for a future you.",
          effects: { fuel: 13 }, damage: { generator: -10 }, delayDays: 3 },
        { chance: 0.2, text: "Half the loop is corroded past use. Four days of work for a mouthful of thrust and a crew that watched you gamble small and win smaller.",
          effects: { fuel: 6 }, damage: { morale: -7 }, delayDays: 4 }
      ]
    }
  ]
},
{
  id: "adrift-003",
  title: "The Ferryman",
  category: "deep-space",
  zones: ["adrift"],
  once: false,
  text: "A bulk hauler answers your drift beacon — the Charon's Purse, crewed by people who look like the inside of a wallet. Her captain names his prices for fuel and smiles at all of them. There's also the ice dwarf two days off your line, offering physics, which at least doesn't smile.",
  choices: [
    {
      label: "Sell a crew contract. One berth, paid in fuel",
      detail: "The Purse is always hiring. That's the worrying part",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 1, text: "Delacroix volunteers so nobody is chosen. She signs at their airlock and salutes with the wrong hand — a joke, her last one aboard. The fuel is good. Nobody says her name at mess for a week, and then someone does, and that's worse.",
          effects: { crew: -1, fuel: 16 }, damage: { morale: -18 } }
      ]
    },
    {
      label: "Empty the pantry into their hold instead",
      requires: { resources: { food: 20 } },
      cost: { food: -14, parts: -3 },
      outcomes: [
        { chance: 0.8, text: "Their quartermaster counts every ration twice and pays in fuel that smells faintly of regret. A fair deal, by the standards of people with none.",
          effects: { fuel: 13 } },
        { chance: 0.2, text: "The transfer hose 'loses pressure' at the ninety-percent mark. Their captain shrugs the shrug of a man who has practiced it. You are in no position to argue, and everyone aboard knows it.",
          effects: { fuel: 9 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Refuse them both and fall at the ice dwarf",
      detail: "Steal momentum from a rock. Astrogation's problem now",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.55, text: "Two days of falling and one savage burn at periapsis. The dwarf donates its momentum without ceremony. Astrogation logs the maneuver as 'theft from a rock' and counts it a win.",
          effects: { fuel: 12 }, delayDays: 2 },
        { chance: 0.25, text: "The approach solution drifts and the burn comes late. You steal less momentum than the rock owed you, and the astrogation rig burns out a gimbal proving it.",
          effects: { fuel: 7 }, damage: { astrogation: -12 }, delayDays: 4 },
        { chance: 0.2, text: "You miss the window. The fallback solution scrapes a whisper of momentum off the flyby and five days off the calendar. The Charon's Purse doesn't wait around to gloat, which is its own species of insult.",
          effects: { fuel: 5 }, damage: { morale: -9 }, delayDays: 5 }
      ]
    }
  ]
}
);
