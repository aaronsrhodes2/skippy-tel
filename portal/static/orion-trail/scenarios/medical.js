/* ORION TRAIL — medical emergencies. Prefix: med- */
SCENARIOS.push(
{
  id: "med-001",
  title: "Fever on Deck Two",
  category: "medical",
  text: "It starts with one crewmate sweating through a duty shift. By morning three bunks are quarantine-taped and the doctor is using the voice doctors use when they don't want to use the other voice.",
  weight: 2,
  choices: [
    {
      label: "Aggressive treatment, full med expenditure",
      cost: { meds: -4 },
      outcomes: [
        { chance: 0.8, text: "The fever breaks ship-wide within days. Expensive, decisive, correct.",
          repair: { morale: 6 } },
        { chance: 0.2, text: "The meds slow it but one case turns septic despite everything. The doctor stops making eye contact.",
          crewDeath: true }
      ]
    },
    {
      label: "Strict quarantine, save the meds",
      detail: "Cold math, warm deck-tape",
      outcomes: [
        { chance: 0.5, text: "The quarantine holds. Two miserable weeks, zero funerals, and a lingering smell of disinfectant.",
          damage: { morale: -8 }, delayDays: 2 },
        { chance: 0.5, text: "It slips the tape line through the air recyclers. More beds fill. The cheap option bills you later.",
          effects: { meds: -2 }, crewDeath: true, damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "med-002",
  title: "Appendicitis, Ninety Days Out",
  category: "medical",
  text: "Your navigator has been quietly clutching her side for two days, because admitting pain is for people near hospitals. The doctor's diagnosis is appendicitis. The nearest surgeon is three months behind you. The doctor has done this operation exactly once, on a cadaver, in school.",
  choices: [
    {
      label: "Operate now, full anesthesia and antibiotics",
      cost: { meds: -3 },
      outcomes: [
        { chance: 0.85, text: "Four hours, one appendix in a specimen jar, one navigator sleeping it off. The doctor's hands only start shaking afterward.",
          repair: { morale: 5 } },
        { chance: 0.15, text: "Zero-g surgery has failure modes the textbooks were too polite to mention. The doctor does everything right. It isn't enough.",
          crewDeath: true }
      ]
    },
    {
      label: "Antibiotics only, hope it cools down",
      cost: { meds: -1 },
      outcomes: [
        { chance: 0.6, text: "The inflammation subsides. She carries a ticking time bomb in her abdomen now, and everyone politely pretends otherwise.",
          damage: { morale: -5 } },
        { chance: 0.25, text: "It ruptures at 0300. Emergency surgery under the worst possible conditions somehow works. The doctor ages a decade.",
          effects: { meds: -2 }, delayDays: 2 },
        { chance: 0.15, text: "It ruptures fast and spreads faster. There was a window, and you spent it hoping.",
          crewDeath: true }
      ]
    },
    {
      label: "Ice packs, prayer, and a scalpel diagram",
      detail: "Free, in the way avalanches are free",
      outcomes: [
        { chance: 0.35, text: "The grumbling appendix settles on its own. The crew watched you gamble a life against a med count and win. They remember.",
          damage: { morale: -8 } },
        { chance: 0.35, text: "Rupture. The doctor performs a miracle with a diagram taped to the bulkhead, burning meds you were saving.",
          effects: { meds: -3 }, delayDays: 3 },
        { chance: 0.3, text: "Some bets you don't get to take back. The scalpel diagram goes out the airlock with her.",
          crewDeath: true, damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "med-003",
  title: "Cargo Shift",
  category: "medical",
  text: "A pallet strap fails during a burn correction and four hundred kilos of spare plating meets a cargo handler's leg. The leg loses. He's conscious, gray, and making jokes, which is how you know it's bad.",
  choices: [
    {
      label: "Surgery and the good painkillers",
      cost: { meds: -3 },
      outcomes: [
        { chance: 0.9, text: "Pins, plates, and pharmaceutical mercy. He'll set off scanners forever and walk fine.",
          repair: { morale: 4 } },
        { chance: 0.1, text: "The bone was pulp below the knee. The leg can't be saved. He learns the nav console instead, and never jokes about it. Almost never.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "Splint it, ration the painkillers",
      cost: { meds: -1 },
      outcomes: [
        { chance: 0.6, text: "It heals crooked. He walks with a hitch and mentions it every single day, forever.",
          damage: { morale: -5 } },
        { chance: 0.4, text: "Infection sets in under the splint. What the cheap fix saved, the follow-up spends.",
          effects: { meds: -2 }, damage: { morale: -8 }, delayDays: 2 }
      ]
    },
    {
      label: "Rig traction from cargo netting and winches",
      detail: "Engineering-grade orthopedics",
      cost: { parts: -1, power: -5 },
      outcomes: [
        { chance: 0.5, text: "The rig holds the bone straighter than the med bay could have. The engineer is insufferable about it for weeks.",
          repair: { morale: 3 } },
        { chance: 0.5, text: "The rig slips during a course correction at 0200. Resetting a half-healed femur costs meds and a scream the whole deck hears.",
          effects: { meds: -2 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "med-004",
  title: "The Bends",
  category: "medical",
  text: "The EVA went fine. The decompression schedule didn't — somebody rushed the airlock cycle to make dinner. Now your hull tech's joints are full of nitrogen bubbles and she's describing the pain very calmly, which the doctor says is the frightening part.",
  choices: [
    {
      label: "Recompress her in the airlock, slow cycle",
      detail: "Burns air and power by the tank",
      cost: { oxygen: -10, power: -8 },
      outcomes: [
        { chance: 0.8, text: "Twelve hours in a metal closet breathing expensive oxygen. The bubbles dissolve. She makes dinner anyway, out of spite.",
          repair: { morale: 3 } },
        { chance: 0.2, text: "One cycle doesn't clear it. You run a second, watching the oxygen gauge like it owes you money.",
          effects: { oxygen: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Anti-inflammatories and a mask",
      cost: { meds: -2, oxygen: -5 },
      outcomes: [
        { chance: 0.7, text: "The slow chemical route works. She's grounded from EVA for weeks and haunts the corridors like an aggrieved ghost.",
          delayDays: 1 },
        { chance: 0.3, text: "The bubbles leave a souvenir — nerve damage in her hands. She can still work. She can't feel a hull seam anymore, and everyone knows it.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Tell her to walk it off",
      outcomes: [
        { chance: 0.4, text: "The bubbles were small. She's fine. She heard what you said, though, and so did everyone else.",
          damage: { morale: -6 } },
        { chance: 0.35, text: "By nightfall she can't feel her legs. The scramble to treat it late costs triple what treating it early would have.",
          effects: { meds: -3 }, damage: { morale: -10 } },
        { chance: 0.25, text: "A bubble reaches somewhere it absolutely cannot be. She walked it off — straight out of the crew roster.",
          crewDeath: true }
      ]
    }
  ]
},
{
  id: "med-005",
  title: "Glow Sickness",
  category: "medical",
  requires: { systemDamaged: ["hull"] },
  text: "The hull was the radiation shielding. The hull has holes. The dosimeters have been quietly climbing for days, and now two crew are vomiting on schedule and losing hair off-schedule. The doctor uses the word 'marrow' in a tone you don't like.",
  choices: [
    {
      label: "Full anti-radiation protocol, everyone",
      cost: { meds: -4 },
      outcomes: [
        { chance: 0.8, text: "Chelators, blockers, and blood work twice a day. Counts recover. The med cabinet looks like a crime scene, but everyone's hair grows back.",
          repair: { morale: 4 } },
        { chance: 0.2, text: "Two doses were past expiry — the labels lied. One crewman stays sick for weeks while the doctor stretches what's left.",
          damage: { morale: -6 }, delayDays: 2 }
      ]
    },
    {
      label: "Pack everyone into the shielded core",
      detail: "Water tanks make decent shielding. Cramped, thirsty shielding.",
      cost: { power: -10, water: -8 },
      outcomes: [
        { chance: 0.55, text: "Eight people, one compartment, water bladders lining the walls. Doses drop. Friendships strain. Nobody dies, and nobody makes eye contact for a week.",
          damage: { morale: -5 } },
        { chance: 0.3, text: "The shielding works late. One crewman's count crashes first and takes real meds to pull back.",
          effects: { meds: -2 }, delayDays: 2 },
        { chance: 0.15, text: "For one of them the dose was already banked days ago. No shielding retroactively works. The doctor explains half-lives to an empty bunk.",
          crewDeath: true }
      ]
    },
    {
      label: "Log the exposure, keep flying",
      outcomes: [
        { chance: 0.5, text: "The doses were survivable. The crew learns you rolled dice with their bone marrow, and the ship gets very quiet.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "Weeks later the sickness blooms properly, and you pay full price plus interest.",
          effects: { meds: -3 }, damage: { morale: -8 }, delayDays: 2 },
        { chance: 0.2, text: "One crewman's log entry ends mid-sentence. Radiation doesn't negotiate and never did.",
          crewDeath: true }
      ]
    }
  ]
},
{
  id: "med-006",
  title: "Protein Batch Forty",
  category: "medical",
  text: "The recycler-grown protein from batch forty tastes, per the crew, 'like regret.' Six hours later half the ship is doubled over. The other half ate batch thirty-nine and is being extremely smug about it.",
  choices: [
    {
      label: "Antiemetics all around",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.75, text: "Chemistry beats biology. Everyone's back on shift by morning, glaring at the recycler.",
          repair: { morale: 2 } },
        { chance: 0.25, text: "The meds work but the contaminated batch is bigger than labeled. More of the larder goes out the airlock, plus a second round of doses.",
          effects: { food: -10, meds: -1 } }
      ]
    },
    {
      label: "Purge the whole batch and fast a day",
      cost: { food: -15 },
      outcomes: [
        { chance: 0.7, text: "Stomachs settle. The crew is hungry, mutinous, and alive, in roughly that order.",
          damage: { morale: -4 } },
        { chance: 0.3, text: "Fasting on an already-poisoned gut goes badly. Dehydration spiral, IV bags, one lost day.",
          effects: { water: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Serve it anyway — dinner is dinner",
      outcomes: [
        { chance: 0.4, text: "Guts adapt. Batch forty becomes an initiation rite and a war story. Morale survives, barely, on gallows pride.",
          damage: { morale: -5 } },
        { chance: 0.6, text: "Half the ship goes down hard. The head becomes contested territory. You spend meds, water, and dignity in bulk.",
          effects: { meds: -2, water: -8 }, damage: { morale: -8 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "med-007",
  title: "Dysentery",
  category: "medical",
  weight: 2,
  text: "A biofilm in the water reclaimer has been seeding the lines for a week. The doctor confirms it with a straight face: dysentery. Aboard a starship. Two hundred million kilometers from the nearest river to ford. The crew does not find the historical resonance amusing. Not from in there, anyway.",
  choices: [
    {
      label: "Full antibiotic course and electrolytes",
      cost: { meds: -3, water: -10 },
      outcomes: [
        { chance: 0.85, text: "Nobody dies of dysentery on your watch. The doctor flushes the lines, frames the culture dish, and labels it 'the ancestral enemy.'",
          repair: { morale: 5 } },
        { chance: 0.15, text: "One crewman was sicker than he admitted — pride, the other epidemic. He pulls through, slowly, expensively.",
          damage: { morale: -5 }, delayDays: 2 }
      ]
    },
    {
      label: "Boil everything, rehydrate, ride it out",
      detail: "The 1848 protocol, now with electric kettles",
      cost: { water: -15, power: -8 },
      outcomes: [
        { chance: 0.6, text: "A week of misery and sterilized water. The outbreak burns itself out. The reclaimer receives a cleaning described in the log as 'punitive.'",
          damage: { morale: -6 } },
        { chance: 0.25, text: "It lingers in two crew who need the real drugs after all, plus recovery time.",
          effects: { meds: -2 }, delayDays: 2 },
        { chance: 0.15, text: "You have a fusion drive, a hydroponics bay, and a computer that plays chess. And a crewmate has died of dysentery. In space. The log entry takes three tries to type.",
          crewDeath: true }
      ]
    },
    {
      label: "Quarantine the head and hope",
      outcomes: [
        { chance: 0.45, text: "Immune systems win the siege unassisted. The crew emerges hollow-eyed and speaks of the aft head the way veterans speak of trenches.",
          damage: { morale: -8 } },
        { chance: 0.25, text: "It slips the quarantine through the one system you can't tape off: the water itself.",
          effects: { water: -12, meds: -2 }, damage: { morale: -8 } },
        { chance: 0.3, text: "Humanity crossed an ocean of vacuum, split the atom, grew steak in a vat — and one of yours still dies of dysentery. Somewhere, a covered wagon nods in recognition.",
          crewDeath: true, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "med-008",
  title: "Two Heartbeats",
  category: "medical",
  text: "The doctor requests a private word. Your comms officer is pregnant — planned, apparently, insofar as anything is planned out here. Due in seven months. That's one more mouth on a fixed manifest, one crew member off EVA rotation, and the first genuinely new thing to happen aboard since Earth.",
  choices: [
    {
      label: "Announce it ship-wide, start a rations pool",
      cost: { food: -10 },
      outcomes: [
        { chance: 0.7, text: "The crew hasn't smiled like this in months. Someone is already welding a crib. Hope, it turns out, weighs nothing and costs everything else.",
          repair: { morale: 12 } },
        { chance: 0.3, text: "Hope is contagious. So is the idea — two other couples start 'planning.' The manifest math gets aspirational.",
          effects: { food: -8 }, repair: { morale: 8 } }
      ]
    },
    {
      label: "Quiet support, adjust the manifest discreetly",
      cost: { food: -5 },
      outcomes: [
        { chance: 0.6, text: "Handled like cargo: logged, allocated, secured. She appreciates the calm. The spreadsheet does not judge.",
          repair: { morale: 4 } },
        { chance: 0.4, text: "Word gets out that you tried to keep it quiet, and the crew decides that means you consider good news a liability.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "Give the cold arithmetic speech",
      detail: "Mouths versus margins, aloud, to her face",
      outcomes: [
        { chance: 0.5, text: "She hears the numbers and volunteers her own ration cuts before you finish. The crew respects the honesty. Mostly.",
          damage: { morale: -4 } },
        { chance: 0.5, text: "The doctor calls you a machine, in the mess, at volume. The word 'captain' is used the way other people use profanity.",
          damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "med-009",
  title: "The Ship That Never Sleeps",
  category: "medical",
  text: "Nobody's slept properly in nine days. The doctor blames the light cycle, the engine harmonics, the ambient dread — pick one. The night shift plays cards with the day shift because both are awake. Reaction times are down, tempers are up, and someone reprogrammed the mess dispenser to only serve coffee.",
  choices: [
    {
      label: "Sedatives, scheduled and supervised",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.7, text: "Chemical night falls over the ship. Ten hours of enforced silence, and the crew wakes up human again.",
          repair: { morale: 6 } },
        { chance: 0.3, text: "Half the crew sleeps beautifully. The other half needs escalating doses and starts hoarding pills like currency.",
          effects: { meds: -2 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Power down to a hard night-cycle, half-shifts",
      cost: { power: -12 },
      outcomes: [
        { chance: 0.65, text: "Dim corridors, dead screens, mandatory dark. Circadian rhythms crawl back to their posts. Progress slows; sanity doesn't.",
          repair: { morale: 5 }, delayDays: 1 },
        { chance: 0.35, text: "Half the crew adapts. The other half lies awake in the dark listening to the hull tick, which is worse.",
          damage: { morale: -5 }, delayDays: 3 }
      ]
    },
    {
      label: "Coffee and denial",
      outcomes: [
        { chance: 0.4, text: "Humans are adaptable. The crew settles into a red-eyed, twitchy equilibrium that technically counts as functioning.",
          damage: { morale: -5 } },
        { chance: 0.6, text: "An exhausted tech skips step four of a nine-step checklist. Step four mattered. Life support wheezes for a week.",
          damage: { lifeSupport: -15, morale: -8 } }
      ]
    }
  ]
},
{
  id: "med-010",
  title: "Allergic to the Ship",
  category: "medical",
  text: "Your hydroponics tech is allergic to the ship. Specifically, to the resin off-gassing from the deck-three panel adhesive — a compound she'll be sealed in with for the rest of the voyage. Her eyes have swollen to slits. The doctor's diagnosis is 'move to a different spaceship.'",
  choices: [
    {
      label: "Antihistamines, indefinitely",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.75, text: "Managed, not cured. She breathes, works, and rattles like a pill bottle when she jogs.",
          repair: { morale: 2 } },
        { chance: 0.25, text: "Her tolerance climbs. The doses climb after it. The med cabinet develops a standing appointment.",
          effects: { meds: -2 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Tear out and reseal the offending panels",
      cost: { parts: -3, power: -6 },
      outcomes: [
        { chance: 0.7, text: "Two days of grinding, sealing, and swearing. The allergen is gone at the source. She cries, for once from gratitude.",
          repair: { morale: 4 } },
        { chance: 0.3, text: "The resin is in more panels than the schematics admit. You reseal deck three; decks four through six file a grievance via her sinuses.",
          effects: { meds: -1 }, damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Reassign her to the one clean compartment",
      outcomes: [
        { chance: 0.55, text: "She lives in the aft cargo bay now, like a beloved exile. Her plants miss her. The crew calls it 'the embassy.'",
          damage: { morale: -6 } },
        { chance: 0.45, text: "A duty rotation puts her back on deck three for one hour. Anaphylaxis doesn't do exceptions. The epinephrine sprint costs you dearly.",
          effects: { meds: -3 }, damage: { morale: -8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "med-011",
  title: "The Paralysis",
  category: "medical",
  text: "Your gunner woke up unable to move his legs. Every scan says nothing is wrong — nerves fire, reflexes answer, the spine is a textbook illustration. The doctor pulls you aside: the injury isn't in his body. He watched Earth's feed go dark on his shift. The legs quit three days later.",
  choices: [
    {
      label: "Treat it as real: bed rest, meds, dignity",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.7, text: "You treat the injury he has instead of the one the scanner sees. Two weeks later he walks to the mess unassisted. Nobody applauds. Everybody notices.",
          repair: { morale: 6 }, delayDays: 1 },
        { chance: 0.3, text: "The meds treat a body that isn't broken. The part that is broken doesn't take injections.",
          damage: { morale: -6 }, delayDays: 2 }
      ]
    },
    {
      label: "The doctor sits with him, shift after shift",
      detail: "Talk therapy on a ship with no spare hours",
      outcomes: [
        { chance: 0.55, text: "On the sixth evening he finally says Earth's name out loud, and something unclenches. He stands the next morning. The doctor sleeps for fourteen hours.",
          repair: { morale: 8 }, delayDays: 2 },
        { chance: 0.45, text: "He retreats further in. Now the doctor's shifts are missing too, and the med bay has a permanent resident.",
          damage: { morale: -8 }, delayDays: 3 }
      ]
    },
    {
      label: "Call it malingering, order him up",
      outcomes: [
        { chance: 0.3, text: "Fury works where medicine didn't — he stands up to tell you exactly what he thinks of you. Ugly cure. It holds. The speech is memorable.",
          damage: { morale: -8 } },
        { chance: 0.7, text: "He doesn't stand. The crew watches you shout at a man whose legs don't work, and something in the chain of command quietly rusts through.",
          damage: { morale: -15 } }
      ]
    }
  ]
},
{
  id: "med-012",
  title: "Stone Cold",
  category: "medical",
  text: "Months of low gravity have been quietly stripping calcium from everyone's bones and banking it in your engineer's kidneys. The withdrawal comes due mid-shift: he folds up screaming next to the reactor housing. The doctor's scan shows a stone the size of bad news.",
  choices: [
    {
      label: "Painkillers and forced hydration",
      cost: { meds: -2, water: -12 },
      outcomes: [
        { chance: 0.8, text: "Three liters a day and pharmaceutical patience. The stone passes. He names it. Nobody asks what.",
          repair: { morale: 2 } },
        { chance: 0.2, text: "The stone lodges instead of passing. More drugs, more waiting, more inventive profanity from the med bay.",
          effects: { meds: -1 }, delayDays: 2 }
      ]
    },
    {
      label: "Improvise lithotripsy with the sensor array",
      detail: "Focused ultrasound, aimed by a hopeful doctor",
      requires: { systemOk: ["sensors"] },
      cost: { power: -8 },
      outcomes: [
        { chance: 0.65, text: "The sensor emitter shatters the stone like cheap ceramic. The doctor writes it up for a journal that no longer exists.",
          repair: { morale: 4 } },
        { chance: 0.35, text: "The focus drifts. The stone breaks, but so does something softer. He recovers, bruised inside and out, on painkillers you meant to save.",
          effects: { meds: -2 }, damage: { morale: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "He passes it the old-fashioned way",
      outcomes: [
        { chance: 0.5, text: "Three days of howling that the whole crew can hear through two bulkheads. The stone passes. Team-building, of the worst available kind.",
          damage: { morale: -8 }, delayDays: 1 },
        { chance: 0.5, text: "Blockage, then infection, then a fever that turns a plumbing problem into a systemic one. Everything you saved gets spent, with penalties.",
          effects: { meds: -3, water: -8 }, damage: { morale: -6 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "med-013",
  title: "Bare Shelves",
  category: "medical",
  requires: { resourceBelow: { meds: 4 } },
  text: "A scratch from a cargo strut. She didn't report it — why would she, over a scratch. Four days later the red lines are marching up her arm and her fever is 40 and climbing. The doctor opens the med cabinet, and the cabinet says what empty cabinets say.",
  choices: [
    {
      label: "Use the last of everything",
      requires: { resources: { meds: 3 } },
      cost: { meds: -3 },
      outcomes: [
        { chance: 0.8, text: "Every remaining vial, every expired half-dose, one arm saved. The cabinet is bare now. The whole crew starts being very, very careful.",
          repair: { morale: 4 } },
        { chance: 0.2, text: "Too little, four days too late. The doctor holds two empty vials and does the arithmetic of blame all night.",
          crewDeath: true }
      ]
    },
    {
      label: "Culture crude antibiotics from hydroponics mold",
      detail: "Penicillin, basically. Mostly. Probably.",
      cost: { water: -10, power: -10, food: -5 },
      outcomes: [
        { chance: 0.55, text: "The doctor and the hydroponics tech brew medicine in a food-grade vat like moonshiners with a microscope. It works. History repeats as farce, then as a cure.",
          repair: { morale: 5 } },
        { chance: 0.25, text: "The batch is weak. It slows the sepsis without stopping it, buying days you pay for in every other resource.",
          effects: { meds: -1 }, delayDays: 2 },
        { chance: 0.2, text: "The mold makes something, but not penicillin. Fleming got lucky. You didn't.",
          crewDeath: true }
      ]
    },
    {
      label: "Debride, cauterize, hope",
      outcomes: [
        { chance: 0.4, text: "Fire, a sharpened spoon, and a twenty-six-year-old immune system. She survives out of what the doctor's notes call 'sheer administrative spite.'",
          damage: { morale: -6 } },
        { chance: 0.25, text: "The infection outruns the knife. The tourniquet call gets made at 0400 by lamplight. She lives. The arm stays behind.",
          damage: { morale: -10 }, delayDays: 1 },
        { chance: 0.35, text: "Sepsis wins the race it was always going to win. A scratch. It started as a scratch.",
          crewDeath: true, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "med-014",
  title: "The Placebo Protocol",
  category: "medical",
  requires: { resourceBelow: { meds: 4 } },
  text: "The med cabinet is nearly empty and the crew knows it, which is why sick call has tripled. Headaches, back spasms, a rash nobody can find twice. The doctor shows you a tray of saline vials with impressive handwritten labels and asks, quietly, how you feel about lying.",
  choices: [
    {
      label: "Back the doctor's theater",
      detail: "Saline, ceremony, and a confident bedside manner",
      outcomes: [
        { chance: 0.6, text: "The vials work flawlessly, being water. Symptoms retreat before the sheer authority of a well-labeled syringe. The doctor feels filthy. The ship feels fine.",
          repair: { morale: 6 } },
        { chance: 0.4, text: "A crewman reads the label under UV light because of course someone does. Now every past treatment is suspect. Trust, it turns out, was also stored in that cabinet.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Announce the shortage honestly",
      outcomes: [
        { chance: 0.5, text: "The crew takes it like professionals: complaints ration themselves, and a corporal starts a stretching class that is mandatory and mildly resented.",
          damage: { morale: -5 } },
        { chance: 0.5, text: "Knowing there's no net makes every twinge a catastrophe. Hypochondria spreads faster than any pathogen you've shipped with.",
          damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Synthesize basics in the fabricator",
      detail: "Aspirin and antiseptic, the hard way",
      cost: { power: -15, parts: -1 },
      outcomes: [
        { chance: 0.7, text: "The fabricator runs hot for two days and produces aspirin, antiseptic, and something the doctor labels 'optimism, 200mg.' The shelves are no longer bare.",
          effects: { meds: 3 } },
        { chance: 0.3, text: "The batch comes out contaminated with lubricant residue. Two days of power, spent to make the world's most expensive hazardous waste.",
          damage: { morale: -6 } }
      ]
    }
  ]
}
);
