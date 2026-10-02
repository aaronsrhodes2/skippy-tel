/* ORION TRAIL — derelicts & salvage. Prefix: der- */
SCENARIOS.push(
{
  id: "der-001",
  title: "The Silent Freighter",
  category: "derelicts",
  text: "A bulk freighter hangs dead ahead, running lights off, cargo doors ajar. No transponder. No distress call. No reason for either, which is itself a reason.",
  sensorWarning: "Large uncharted mass on intercept-adjacent vector.",
  choices: [
    {
      label: "Board with a full salvage team",
      detail: "Greed, but organized",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.55, text: "Jackpot. Sealed stores, intact parts, and a coffee tin someone will fight over later.",
          effects: { food: 15, parts: 4, meds: 2 } },
        { chance: 0.30, text: "The hold is stripped bare. Whoever got here first even took the door handles.",
          effects: { fuel: -4 } },
        { chance: 0.15, text: "The airlock cycles on its own behind the team. Someone doesn't make it back to the boarding tube.",
          effects: { parts: 2 }, crewDeath: true, damage: { morale: -8 } }
      ]
    },
    {
      label: "Scan thoroughly, then decide",
      requires: { systemOk: ["sensors"] },
      outcomes: [
        { chance: 1, text: "Deep scan shows vacuum-frozen crew and an intact aft store. You take what the dead no longer need, carefully.",
          effects: { food: 10, parts: 2 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Leave it. Some doors stay shut",
      outcomes: [
        { chance: 1, text: "The freighter shrinks in the aft viewport. Nobody argues. Nobody quite relaxes either.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "der-002",
  title: "Eight Thousand Berths",
  category: "derelicts",
  text: "The colony ship Prosperity lists to starboard, her half-built centrifuge folded like a broken wing. Eight thousand cryo berths. She launched before you were born and got exactly this far. Something aboard is still drawing power, which is either very good news or the other kind.",
  choices: [
    {
      label: "Board the cryo decks",
      detail: "Find whatever is still running",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.5, text: "Intact stores and live power cells. The sleepers failed decades ago — eight thousand quiet faces behind frost. The team works fast and doesn't look up.",
          effects: { food: 12, power: 10, parts: 2 }, damage: { morale: -5 } },
        { chance: 0.3, text: "The bays are tombs and the stores are dust. You salvage the medical lockers and leave the lights on behind you. It seems right.",
          effects: { meds: 3 }, damage: { morale: -8 } },
        { chance: 0.2, text: "An automated quarantine protocol seals a deck with your team inside. Cutting them out takes two days and most of your patience.",
          effects: { parts: 1 }, delayDays: 2, damage: { morale: -4 } }
      ]
    },
    {
      label: "Siphon her reactor from outside",
      detail: "No boarding, no ghosts",
      cost: { fuel: -3 },
      outcomes: [
        { chance: 0.7, text: "The umbilical holds. Her reactor gives up its charge like it was waiting for someone to ask.",
          effects: { power: 15 } },
        { chance: 0.3, text: "Feedback arc halfway through the transfer. You get some charge and a scorched relay panel.",
          effects: { power: 5 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Mark her position and move on",
      outcomes: [
        { chance: 1, text: "Eight thousand berths drop astern. Someone updates the chart annotation from UNKNOWN to KNOWN and nobody feels better.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "der-003",
  title: "The Gunship Vigilant",
  category: "derelicts",
  text: "A navy corvette, dark but not dead: her point-defense grid still tracks you, patient as a headstone. Lockdown protocols. Her crew died at their posts protecting something, and the ship intends to go on protecting it without them.",
  choices: [
    {
      label: "Shoot out the point-defense nodes",
      detail: "Rude, but effective",
      requires: { systemOk: ["weapons"] },
      cost: { power: -5 },
      outcomes: [
        { chance: 0.6, text: "Four clean hits and the grid goes blind. The armory and machine shops are untouched. Military spec: ugly, overbuilt, exactly what you need.",
          effects: { parts: 5, fuel: 4 } },
        { chance: 0.4, text: "The grid gets one burst off before it dies. You board anyway, bleeding atmosphere, and take what's owed.",
          effects: { parts: 3 }, damage: { hull: -15 } }
      ]
    },
    {
      label: "Drift in dead-slow, cold and quiet",
      detail: "Fool the targeting logic",
      outcomes: [
        { chance: 0.45, text: "The grid never twitches. Inside: sealed rations, a machine shop, and a wall of names you don't read twice.",
          effects: { parts: 3, meds: 2 } },
        { chance: 0.35, text: "A thruster puff wakes the grid at forty meters. The graze along your flank sounds worse than it is. Barely.",
          damage: { hull: -15, morale: -5 } },
        { chance: 0.2, text: "Full target lock. You burn away under fire with nothing but a new respect for the dead crew's maintenance schedule.",
          effects: { fuel: -5 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Not worth dying for",
      outcomes: [
        { chance: 1, text: "The corvette holds her post as you pass. Whatever she's guarding, she can keep it. The crew mutters about the fuel she was carrying.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-004",
  title: "The Elysian Dawn",
  category: "derelicts",
  text: "A luxury liner, gilt lettering flaking off ten decks of staterooms. The orchestra pit still has its instruments clipped down. The lifeboats are all gone — the rich left early, and they left everything.",
  choices: [
    {
      label: "Raid the galleys and wine stores",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.6, text: "Real coffee. Real sugar. Vacuum-sealed delicacies meant for people who never ate them. Dinner tonight is the best morale officer you've ever had.",
          effects: { food: 12 }, repair: { morale: 6 } },
        { chance: 0.4, text: "The galleys were looted years ago. You scrape the crew pantries and pretend the caviar tins weren't already empty.",
          effects: { food: 4 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Strip fixtures and wiring",
      detail: "Luxury is just parts wearing jewelry",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.7, text: "Gold-plated conduit is still conduit. The chandelier ballast alone re-stocks half a shelf.",
          effects: { parts: 4 } },
        { chance: 0.3, text: "Mostly decorative junk under the shine. A day of prying gets you brass, regret, and a little copper.",
          effects: { parts: 1 }, delayDays: 1 }
      ]
    },
    {
      label: "Keep flying",
      outcomes: [
        { chance: 1, text: "Ten decks of paradise recede in the viewport. The crew eats reconstituted protein in silence and thinks about ballrooms.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "der-005",
  title: "Station Long Sight",
  category: "derelicts",
  text: "A deep-survey station, antenna arrays fanned like a dead moth's wings. Somebody kept the telescopes running long after the food ran out — the final log entries are star charts, annotated in a steadily worsening hand.",
  choices: [
    {
      label: "Download the survey archives",
      detail: "Dead astronomers, living data",
      requires: { systemOk: ["sensors"] },
      cost: { power: -5 },
      outcomes: [
        { chance: 1, text: "Decades of charted hazards, refined ephemerides, corrected course tables. The last entry is addressed 'to whoever comes.' You came.",
          repair: { astrogation: 20, sensors: 10 } }
      ]
    },
    {
      label: "Salvage the labs",
      outcomes: [
        { chance: 0.6, text: "Pharmaceutical stocks and precision instruments, all neatly inventoried. Scientists die tidy.",
          effects: { meds: 3, parts: 2 } },
        { chance: 0.4, text: "A sample fridge vents something ugly when the seal breaks. The team retreats coughing and burns through supplies scrubbing suits.",
          effects: { meds: -2 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Leave the astronomers to their stars",
      outcomes: [
        { chance: 1, text: "The arrays keep staring at nothing as you pass. Your navigator mentions, twice, what those charts might have been worth.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "der-006",
  title: "Arkwright's Hold",
  category: "derelicts",
  text: "A generation ship's agricultural ring, sheared clean off its spine by something with opinions. Somewhere inside is the seed vault: vacuum-sealed racks of everything Earth used to grow. A billion futures in little foil envelopes, and nobody left to plant them.",
  zones: ["outer","deep"],
  choices: [
    {
      label: "Deep-scan for the vault and cut straight in",
      requires: { systemOk: ["sensors"] },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.8, text: "The scan finds the vault on the second pass. You cut through three bulkheads and come back carrying spring, summer, and fall.",
          effects: { food: 18 } },
        { chance: 0.2, text: "The vault section vented long ago. Half the racks are freeze-ruined. You take what survived and don't discuss the rest.",
          effects: { food: 8 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Search the ring deck by deck",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.5, text: "Six hours of dead orchards, then the vault door. Inside, the future smells like clean paper and possibility.",
          effects: { food: 14 } },
        { chance: 0.3, text: "The ring is a maze of collapsed grow-tiers. You find the vault eventually. 'Eventually' costs you two days.",
          effects: { food: 8 }, delayDays: 2 },
        { chance: 0.2, text: "A rack tier collapses in the dark on your point man. He'll live, mostly thanks to the med kit he was carrying.",
          effects: { food: 6, meds: -2 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Leave it for a ship with more time",
      outcomes: [
        { chance: 1, text: "Everyone knows what was in there. Nobody says it. The hydroponics bay gets a lot of thoughtful visitors this week.",
          damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "der-007",
  title: "The Tanker Deepwater",
  category: "derelicts",
  text: "A fuel tanker adrift with her tanks half-full and one flank scorched black. Somebody already tried opening her the fast way. Your own fuel gauge has never looked more eloquent.",
  sensorWarning: "Large uncharted mass ahead. Strong volatiles signature.",
  choices: [
    {
      label: "Vent the tanks from range, then collect",
      detail: "Precise shot, safe distance",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.8, text: "One surgical hit bleeds the pressure off clean. You scoop frozen fuel out of the void like harvest.",
          effects: { fuel: 15 } },
        { chance: 0.2, text: "Residual vapor ignites on the second shot. The fireball is brief, gorgeous, and expensive. You salvage what didn't burn.",
          effects: { fuel: 6 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Dock and pump direct",
      detail: "All of it. Every drop",
      outcomes: [
        { chance: 0.55, text: "Four nervous hours on the pumps. The tanks give up everything. Nobody exhales until the umbilical is stowed.",
          effects: { fuel: 20 } },
        { chance: 0.25, text: "The transfer seals fail mid-pump, spraying fuel across your hull. You save some of it. The paint, less so.",
          effects: { fuel: 8 }, damage: { hull: -15 } },
        { chance: 0.2, text: "Static discharge in the transfer line. The explosion takes the pump bay, a chunk of your hull, and the crewman on the manifold.",
          effects: { fuel: 5 }, crewDeath: true, damage: { hull: -25, morale: -8 } }
      ]
    },
    {
      label: "Too many scorch marks. Pass",
      outcomes: [
        { chance: 1, text: "Half a tanker's worth of fuel falls astern. The fuel gauge watches you do it.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "der-008",
  title: "Hospital Ship Clemency",
  category: "derelicts",
  text: "A hospital ship, white hull gone grey, the caduceus on her flank pitted to a shadow. Her quarantine beacon still chirps on a loop. Whatever the wards were full of, the doctors didn't win. But meds — whole decks of meds.",
  choices: [
    {
      label: "Scan the wards before anyone suits up",
      requires: { systemOk: ["sensors"] },
      cost: { oxygen: -3 },
      outcomes: [
        { chance: 1, text: "The bioscan maps clean corridors to the pharmacy stores. You take everything with an intact seal and touch nothing else. Nobody describes the wards afterward.",
          effects: { meds: 5 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Suit up and go in blind",
      outcomes: [
        { chance: 0.5, text: "The pharmacy deck is sealed and full. The route there is neither. The team comes back rich and quiet.",
          effects: { meds: 6 }, damage: { morale: -4 } },
        { chance: 0.3, text: "Contaminated sections force the long way around, and the long decon after. Two days in the airlock, scrubbing.",
          effects: { meds: 2 }, delayDays: 2, damage: { morale: -3 } },
        { chance: 0.2, text: "A suit seal fails in the isolation ward. Your medic spends a week and most of your stock making sure it stays a scare.",
          effects: { meds: -3 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Respect the quarantine",
      outcomes: [
        { chance: 1, text: "The beacon chirps its warning long after you've passed. The med locker inventory does not improve by itself.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-009",
  title: "The Ship That Almost",
  category: "derelicts",
  text: "Another refugee ship. Same conversion job as yours — cargo hauler, retrofit cryo, hand-welded hydroponics. She made it exactly as far as you have. The hull breach is small. It only needed to be small.",
  zones: ["deep"],
  choices: [
    {
      label: "Board her. Learn what killed her",
      requires: { minCrew: 2 },
      outcomes: [
        { chance: 0.6, text: "Micrometeorite through the water recycler. They died of thirst next to a fixable fault. Your engineer takes their spare filters and doesn't speak for two days.",
          effects: { parts: 3, water: 8 }, damage: { morale: -8 } },
        { chance: 0.4, text: "You find the captain's log. The final entry is a repair checklist nobody was left to finish. You take their stores and their lesson.",
          effects: { food: 6, parts: 2 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Take her fuel and skip the tour",
      outcomes: [
        { chance: 0.7, text: "The siphon runs clean. Nobody boards, nobody looks, and the tanks were fuller than yours. All of that is something.",
          effects: { fuel: 8 }, damage: { morale: -4 } },
        { chance: 0.3, text: "Someone looks through a viewport during the transfer anyway. There was a child's drawing taped by the airlock. Word gets around.",
          effects: { fuel: 3 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Full burn past. Don't let the crew see",
      outcomes: [
        { chance: 1, text: "They see. Of course they see. The silhouette is your silhouette, and everyone aboard does the same arithmetic at the same time.",
          effects: { fuel: -3 }, damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-010",
  title: "The Parking Orbit",
  category: "derelicts",
  text: "An evacuation staging orbit from the early collapse: hundreds of hulls that never got clearance, never got fuel, never got lucky. A city's worth of dead ships packed close enough to walk between. Salvage everywhere. Also collisions, everywhere.",
  zones: ["inner"],
  sensorWarning: "Dense debris field ahead — hundreds of contacts, none under power.",
  choices: [
    {
      label: "Thread the field on instruments",
      detail: "Deep salvage, sensor-guided",
      requires: { systemOk: ["sensors"] },
      outcomes: [
        { chance: 0.7, text: "The sensors call every tumbling wreck three seconds early. You pick the field like an orchard and leave by the gap you came in.",
          effects: { parts: 4, food: 6 } },
        { chance: 0.3, text: "A dead shuttle changes its tumble at exactly the wrong moment. The scrape costs plating; the salvage covers some of it.",
          effects: { parts: 2 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Pick at the edges only",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.75, text: "The outer wrecks were looted first but not last. Slim pickings, safely picked.",
          effects: { parts: 2, food: 3 } },
        { chance: 0.25, text: "Even the edges have teeth. A slow-drifting cargo pallet finds your hull the way rain finds a leak.",
          effects: { parts: 1 }, damage: { hull: -5 } }
      ]
    },
    {
      label: "Go around the whole graveyard",
      outcomes: [
        { chance: 1, text: "Two days of detour around a fortune in dead metal. The crew watches the field slide past and quietly appraises it the entire time.",
          effects: { fuel: -3 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "der-011",
  title: "Slow Fire",
  category: "derelicts",
  text: "An ore hauler, holds packed with refined alloy — a fortune in parts. Her reactor is another story: containment failing by degrees, the whole aft section glowing on infrared like a banked coal. You have hours, not days.",
  zones: ["belt","outer"],
  choices: [
    {
      label: "Fast team, in and out",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.6, text: "Twenty minutes, three trips, dosimeters singing the whole time but never screaming. The alloy is beautiful.",
          effects: { parts: 6 } },
        { chance: 0.25, text: "The dosimeters scream on trip two. You pull everyone out early with half a haul and a round of iodine tablets.",
          effects: { parts: 3, meds: -1 }, damage: { morale: -3 } },
        { chance: 0.15, text: "A hot spot nobody mapped. The team gets the alloy and a dose your medic describes as 'survivable, with attention.'",
          effects: { parts: 4, meds: -3 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Cut the whole hold section free and tow it",
      detail: "Slow, greedy, structural",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.6, text: "The cut is clean and the tow is heavy. You strip the hold at a comfortable distance from the glow.",
          effects: { parts: 8 } },
        { chance: 0.4, text: "The cut nicks a stressed member and the section shears wrong, kissing your bow on its way loose. Still worth it. Probably.",
          effects: { parts: 4 }, damage: { hull: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Nothing here is worth a half-life",
      outcomes: [
        { chance: 1, text: "The hauler cooks on behind you. Your engineer keeps a running estimate of the alloy's value for three days, out loud.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-012",
  title: "The Kindness of Strangers",
  category: "derelicts",
  text: "A yacht broadcasting a looped distress call, one life sign flickering aboard. Her hull is patched with plating from six different ships. Either she's the luckiest boat in the void, or she's bait — and somebody keeps re-baiting the hook.",
  choices: [
    {
      label: "Answer with weapons hot",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.6, text: "Two scavenger skiffs break off the moment your targeting pings them. The 'life sign' is a heater wired to a med monitor. You strip the stage set.",
          effects: { parts: 3, food: 4 } },
        { chance: 0.4, text: "They try anyway. It's short. You patch the one hit they landed and inherit their bait, their cache, and their example.",
          effects: { parts: 4, fuel: 3 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Dock and render aid",
      outcomes: [
        { chance: 0.35, text: "There is a survivor — half-starved, wired to a dead man's switch she begs you to disarm. You do. She's crew now, and she doesn't sleep near airlocks.",
          effects: { crew: 1, food: -5 }, repair: { morale: 8 } },
        { chance: 0.45, text: "The trap springs. They take a toll of fuel and stores and let you live — this time. Businesslike, almost polite. Somehow that's worse.",
          effects: { fuel: -6, food: -8, parts: -2 }, damage: { morale: -6 } },
        { chance: 0.2, text: "The trap springs badly. You fight clear of the boarding tube, but not all of you.",
          crewDeath: true, damage: { hull: -15, morale: -8 } }
      ]
    },
    {
      label: "Log the beacon and burn past",
      outcomes: [
        { chance: 1, text: "If it was real, you'll hear her voice on the loop for weeks. If it wasn't, you'll wonder anyway. The radio room gets quieter.",
          damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "der-013",
  title: "The Centrifuge",
  category: "derelicts",
  text: "A ring habitat spinning fast and wrong, stabilizers long dead. Its water reclamation plant was rated for nine hundred people and its tanks read half-full. Matching that rotation in a suit is the kind of math that has casualties in it.",
  choices: [
    {
      label: "Send three out to despin and tap the tanks",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.6, text: "Thruster packs against a dead city's spin. It works. The tanks flow, and the ring slows to something almost peaceful.",
          effects: { water: 15, oxygen: 8 } },
        { chance: 0.25, text: "One suit thruster misfires mid-transfer. Your medic sets the shoulder; the tanks pay for the trouble, mostly.",
          effects: { water: 8, meds: -2 }, damage: { morale: -4 } },
        { chance: 0.15, text: "The despin sheds a panel that finds your hull at rotational velocity. The water is real. So is the dent.",
          effects: { water: 10 }, damage: { hull: -15, morale: -3 } }
      ]
    },
    {
      label: "Grapple and drain from a distance",
      requires: { resources: { parts: 2 } },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.5, text: "The rig holds against the spin, barely, humming like a plucked wire. You drain what the line can carry.",
          effects: { water: 10 } },
        { chance: 0.5, text: "The line flails on the third rotation and comes back at you like a whip. You keep some water and lose some paint.",
          effects: { water: 4 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Let it spin",
      outcomes: [
        { chance: 1, text: "Half-full tanks for nine hundred people, spinning off into the dark. The ration schedule stays exactly as tight as it was.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-014",
  title: "The Salvage Tug Pelican",
  category: "derelicts",
  text: "A heavy salvage tug — ironically, unsalvaged. Her engines are the same mark as yours, one generation newer and mostly intact. Grafting them across means days of EVA surgery. Your engineer is already making the face.",
  choices: [
    {
      label: "Full engine graft",
      detail: "Days of work, real thrust",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "Three days of welding, swearing, and triumph. Your engines run smoother than they have since Earth orbit.",
          repair: { engines: 30 }, delayDays: 3 },
        { chance: 0.3, text: "The manifolds are incompatible in a way the manuals call 'undocumented.' You force a partial fit and four days vanish.",
          repair: { engines: 10 }, delayDays: 4, damage: { morale: -4 } }
      ]
    },
    {
      label: "Strip quick components only",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.8, text: "Pumps, injectors, control boards — a day of easy shopping in a dead man's engine room.",
          effects: { parts: 4 }, delayDays: 1 },
        { chance: 0.2, text: "A fuel line you were assured was purged wasn't. The flash fire is small; the lecture from your engineer is not.",
          effects: { parts: 2 }, damage: { hull: -5, morale: -3 } }
      ]
    },
    {
      label: "Note it in the log and move on",
      outcomes: [
        { chance: 1, text: "Your engineer logs the tug's position, model number, and estimated value in aggressive detail. The entry reads like an accusation.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "der-015",
  title: "The Ice Barge",
  category: "derelicts",
  text: "A municipal ice barge from the first evacuation wave, holds still packed with water ice bound for a city that stopped existing before delivery. Three other ships are already cutting into her. Nobody is waving hello.",
  zones: ["inner","belt"],
  choices: [
    {
      label: "Claim a hold and cut fast",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.6, text: "You take the aft hold and work it hard. The other crews watch, weigh you, and decide against. Ice by the ton.",
          effects: { water: 12, oxygen: 6 } },
        { chance: 0.4, text: "Another crew disputes your hold with a cutting torch and worse manners. You keep half the ice and a new scar on the bow.",
          effects: { water: 6 }, damage: { hull: -5, morale: -4 } }
      ]
    },
    {
      label: "Buy in — trade parts for a worked seam",
      cost: { parts: -3 },
      outcomes: [
        { chance: 1, text: "The nearest crew takes your parts and points you to a seam already softened. Civil, by current standards. Nobody shows anybody their guns.",
          effects: { water: 10, oxygen: 5 } }
      ]
    },
    {
      label: "Three ships means three sets of guns. Leave",
      outcomes: [
        { chance: 1, text: "A mountain of water recedes astern while the ration clock ticks. The crew understands the caution. Understanding is not the same as forgiving.",
          damage: { morale: -3 } }
      ]
    }
  ]
}
);
