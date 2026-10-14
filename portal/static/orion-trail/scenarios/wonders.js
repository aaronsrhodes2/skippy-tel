/* ORION TRAIL — wonders. Prefix: wonder- .
   The universe, for once, is on your side. Big payoffs, light prices (a day, a part, a small risk, or just
   the opportunity cost of picking one gift over another). Rare: weight ~0.7 (engine doubles it for a broken crew). The validator exempts this
   category from the no-free-lunch warning on purpose. `advance: N` jumps the ship N units along the current leg. */
SCENARIOS.push(
{
  id: "wonder-001",
  title: "The Pod",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "They rise out of the dark in a slow, shining line: whales the size of cargo ships, made of light and soft gravity, singing in a key your hull hums back to. A whole pod. They circle the ship once, unhurried, as if to say 'there you are.' The aurora of their bodies fills every viewport.",
  choices: [
    {
      label: "Ride their wake",
      detail: "They move like a river. You could move with them.",
      outcomes: [
        { chance: 0.85, text: "The ship is carried, tenderly, on a current you cannot see. Days of travel fall away behind you. The pod peels off at last, and one of them slides a long, gentle shadow across the bridge, like a hand on a shoulder.",
          advance: 4, repair: { morale: 14 } },
        { chance: 0.15, text: "A wonderful ride. A slightly rough landing. The hull sings for a day, and a seam or two sings with it.",
          advance: 4, repair: { morale: 14 }, damage: { hull: -8 } }
      ]
    },
    {
      label: "Collect what they shed",
      detail: "Rich, luminous, strangely fuel-like.",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 1, text: "A day of patient collection with nets and cans. The pod drifts nearby, quietly pleased to be of use. By evening you have more fuel than you have tanks, and a tin of something that glows and is, the doctor swears, good for the lungs.",
          effects: { fuel: 30, oxygen: 8 }, repair: { morale: 8 }, delayDays: 1 }
      ]
    },
    {
      label: "Just watch",
      outcomes: [
        { chance: 1, text: "You cut the engines and let the light fill the room. Nobody speaks. Somebody cries without noise. When the pod is gone, every person aboard is a little taller.",
          repair: { morale: 22, lifeSupport: 5 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-002",
  title: "The Orchard",
  category: "wonders",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A sealed arboretum drifts in the dark, a great glass wheel, still spinning, still lit. Inside, rows on rows of trees: apples, oranges, pears and some things you would not name. They are in full, impossible fruit. There is a sign on the airlock in a child's hand: PLEASE PICK.",
  choices: [
    {
      label: "Harvest what the ship can carry",
      outcomes: [
        { chance: 0.9, text: "A day, a hundred crates, and everyone sticky to the elbows. The ship smells like a summer you thought was lost. The crew eats a fruit they were told did not exist and sleeps like children.",
          effects: { food: 40, water: 8 }, repair: { morale: 14 }, delayDays: 1 },
        { chance: 0.1, text: "A day, a hundred crates, and one very large wasp. The wasp is a good sport about it. So are the crew. Nobody is hurt, much.",
          effects: { food: 40, water: 8, meds: -1 }, repair: { morale: 10 }, delayDays: 1 }
      ]
    },
    {
      label: "Take a week of rest in the orchard",
      detail: "A garden is a garden. Sit in it.",
      outcomes: [
        { chance: 1, text: "Hammocks in the branches, bare feet on actual grass. The crew rests as it has not rested since Earth. You do not push them. By week's end the ship is full of people who remember what they are for.",
          effects: { food: 20 }, repair: { morale: 30, lifeSupport: 8 }, delayDays: 4 }
      ]
    },
    {
      label: "Take cuttings and seeds",
      detail: "For the hydroponics bay. For later.",
      cost: { power: -4 },
      outcomes: [
        { chance: 1, text: "A day of grafting, soil in the cabin, and a hydroponics bay that is, for the first time in the history of the ship, thriving. Plants line the corridors. The air gets better and the food keeps coming.",
          effects: { food: 15, oxygen: 18 }, repair: { lifeSupport: 12, morale: 6 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-003",
  title: "The Kind Machine",
  category: "wonders",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A battered repair-drone the size of a bus noses up to the airlock, politely. Its hull says SERVICE. Its voice, in the ancient, formal English of a very old warranty, says: 'Our records indicate this vessel is in need of attention. Would you like it? It is free. There is no catch. We have been looking for something to do.'",
  choices: [
    {
      label: "Full overhaul",
      detail: "Everything. Every bolt. Two days.",
      outcomes: [
        { chance: 0.85, text: "Two days of arms, torches, and tiny gleaming tools. The ship comes out of it smelling of solder and new paint. Everything works. Things that did not exist before work. The drone bows, and goes to look for another ship that needs it.",
          repair: { hull: 25, engines: 25, generator: 25, lifeSupport: 25 }, delayDays: 2 },
        { chance: 0.15, text: "Two days of arms, torches, and fussy little instructions. Most of it is perfect. A couple of its upgrades are baffling. The galley now has a dial labelled 'ambience'.",
          repair: { hull: 20, engines: 20, generator: 20, lifeSupport: 20 }, damage: { sensors: -8 }, delayDays: 2 }
      ]
    },
    {
      label: "Just the engines and generator",
      detail: "Quick, focused, back on the trail.",
      outcomes: [
        { chance: 1, text: "Six hours of very careful work on the parts that move you. The drone declines a tip. 'It was a pleasure,' it says, and the ship, in your hands, feels ten years younger.",
          repair: { engines: 35, generator: 35 }, delayDays: 1 }
      ]
    },
    {
      label: "Ask it to teach the crew",
      outcomes: [
        { chance: 1, text: "A class in the engine room, with ancient diagrams and a very patient teacher. By evening the crew has twelve new shortcuts and a stack of notes, and the drone has a small standing ovation. The ship runs a little better for months.",
          repair: { engines: 12, generator: 12, hull: 12, morale: 12 }, effects: { parts: 3 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-004",
  title: "The Slingshot",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A ribbon of bright, folded space curls across your course: a gravity current, smooth and wide, running the way you want to go. Your navigator has gone very pale and very excited. 'It's a river,' she says. 'It's a river in the sky. It would take us nearly all the way, if we thread it right.'",
  choices: [
    {
      label: "Thread the ribbon",
      detail: "Needs good astrogation.",
      requires: { systemOk: ["astrogation"] },
      outcomes: [
        { chance: 0.85, text: "A glorious, terrifying, perfect arc. The stars smear. The engines go quiet for the first time in months, as the ribbon carries you toward the place you were going all along. You land in tears and trailing fuel.",
          advance: 6, effects: { fuel: 8 }, repair: { morale: 12 } },
        { chance: 0.15, text: "A glorious, terrifying, slightly crooked arc. You land a long way ahead, with a ringing in the hull.",
          advance: 6, damage: { hull: -10, astrogation: -8 } }
      ]
    },
    {
      label: "Ride the edge, carefully",
      outcomes: [
        { chance: 1, text: "You skim the ribbon's calm rim, borrowing just a little of its speed. It is not glorious. It is lovely. A good push, and an unbroken ship.",
          advance: 3, repair: { morale: 4 } }
      ]
    },
    {
      label: "Fire a probe in first",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 1, text: "The probe returns, a day late and a wonderful distance away, with a perfect chart of the ribbon. You follow it down the center, with the confidence of a ship that has done this before.",
          advance: 5, repair: { astrogation: 8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-005",
  title: "The Empty Hospital",
  category: "wonders",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A hospital ship, white and clean and utterly empty, hangs in the dark with every light burning. Fifty beds, made. A pharmacy, locked and intact. A sign on the door in six languages: TAKE WHAT YOU NEED. THERE IS ENOUGH. THIS IS WHAT IT WAS FOR.",
  choices: [
    {
      label: "Take what you need",
      outcomes: [
        { chance: 1, text: "A quiet half-day of care and checklists. Meds, bandages, a tray of the good surgical kit, a stack of clean sheets. You take what you need and nothing more. The ship, passing out of range, blinks its lights once, like a goodnight.",
          effects: { meds: 10, parts: 1 }, repair: { morale: 10 }, delayDays: 1 }
      ]
    },
    {
      label: "Take everything",
      detail: "Everything it has. All of it.",
      outcomes: [
        { chance: 0.7, text: "A day of hauling, grunting, trying not to think. Meds up to the eaves, a surgical bay in your hold. The ship sits low and heavy. Nobody feels good about it. Nobody argues.",
          effects: { meds: 16, parts: 3 }, damage: { morale: -4 }, delayDays: 1 },
        { chance: 0.3, text: "A day of hauling. The pharmacy locks itself against a greedy hand halfway through, and the alarm that follows has a gentle, disappointed voice. You leave with most of it, and a lot of explaining to do.",
          effects: { meds: 12, parts: 2 }, damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Rest the crew in the wards",
      detail: "Clean beds. Real doctors' orders.",
      outcomes: [
        { chance: 1, text: "Two days of clean sheets, quiet, warm tea, and a doctor who is only a recording. Everyone sleeps like children. Old injuries mend. Old fears quiet down. By the time you leave, the crew is, remarkably, whole.",
          effects: { meds: 4 }, repair: { morale: 28 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "wonder-006",
  title: "A Small, Tame Star",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A star the size of a house floats across the way, gold and gentle and entirely without malice. It is warm. It hums. It slowly drifts toward the ship, like a cat that has chosen a lap. The crew stands at the viewport with their faces lit, and nobody wants to be the one to ask what it wants.",
  choices: [
    {
      label: "Let it come close",
      outcomes: [
        { chance: 0.85, text: "It hangs outside the bridge for a day. The ship fills with gold. The panels drink it. The air feels like July. Every system hums and every person smiles. When the star drifts off, it leaves a faint warm glow in the metal, and your batteries brimming.",
          effects: { power: 40 }, repair: { morale: 16, generator: 10 }, delayDays: 1 },
        { chance: 0.15, text: "It comes very close. A little too close. A bit of the paint blisters, a panel hums sharply. It does not mean to. It apologizes by pouring a flood of warmth into the batteries.",
          effects: { power: 40 }, repair: { morale: 14 }, damage: { hull: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Fill the cells and the tanks, quickly",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 1, text: "A neat, skilled, enormously profitable afternoon. Every tank, every cell, every battery takes a gulp of sunshine. The star, as if sensing the end of a good visit, wanders off the moment you are done.",
          effects: { power: 50, fuel: 12, oxygen: 10 } }
      ]
    },
    {
      label: "Bask",
      detail: "A whole day, on the deck, in the light.",
      outcomes: [
        { chance: 1, text: "A long, lazy day on deck blankets, faces to the star. Skin, bone, heart, all warm. It is the best day of the trip, and perhaps of the year, and nobody speaks of anything serious until night.",
          effects: { power: 14 }, repair: { morale: 30, lifeSupport: 6 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-007",
  title: "Song of the Void",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "At first it is just a hum in the hull, more felt than heard. Then it has a tune. Then harmony. Something out in the dark is singing, a vast, kind song with no words, and the ship, a little tired, hums back, and the rust on the bulkheads tastes the sound and starts, very quietly, to heal.",
  choices: [
    {
      label: "Sing back",
      detail: "The crew, all together. Badly.",
      outcomes: [
        { chance: 0.9, text: "You are all dreadful. It does not matter. For an hour the ship and the void sing together, and the panels warm, and the pipes ring true. When the song fades, the hull has a gloss on it and the crew has red, wet, grinning faces.",
          repair: { hull: 20, engines: 12, generator: 12, morale: 18 } },
        { chance: 0.1, text: "You are all dreadful. One of you is quite good. The song asks them, politely, to continue. Everything mends, and a very good singer is a bit hoarse for a week.",
          repair: { hull: 20, engines: 12, generator: 12, morale: 12 } }
      ]
    },
    {
      label: "Record it, and listen later",
      cost: { power: -3 },
      outcomes: [
        { chance: 1, text: "You capture it, whole, in a quiet, cold, perfect file. Played softly at night, it does for the crew what a lullaby does for children. Systems quietly mend. People quietly forgive.",
          repair: { morale: 22, sensors: 12, astrogation: 8 } }
      ]
    },
    {
      label: "Let it play, and stay silent",
      outcomes: [
        { chance: 1, text: "You listen, all of you, in the dark, with the lights down. It goes for hours. When it ends, a hush so soft settles on the ship that the first person to speak whispers.",
          repair: { morale: 14, hull: 12, lifeSupport: 12 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-008",
  title: "The Fold",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "Space has a crease in it. A bright, tidy seam, as if the sky were a sheet and someone had folded it in half and pressed it flat. Through the crease, your destination's stars are close enough to touch. The navigator says it will hold for about an hour. Her hands are shaking, with something that may be joy.",
  choices: [
    {
      label: "Fly through the fold",
      detail: "Straight in. Don't blink.",
      outcomes: [
        { chance: 0.8, text: "A moment of dizzy white, a lurch, and every star out the window is somewhere else. A distance that would have taken you three weeks has taken three seconds. You stay seated for a very long time, afterwards, to make sure you are all still in your bodies.",
          advance: 6, repair: { morale: 10 } },
        { chance: 0.2, text: "The fold is rougher than it looked. You arrive, a very long way ahead, scrubbed, shaken, and several panels short. The ship holds. The crew cheers, with their hands over their ears.",
          advance: 6, damage: { hull: -15, engines: -8 }, repair: { morale: 6 } }
      ]
    },
    {
      label: "Send a probe through, then follow its path",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 1, text: "The probe returns with a small, cheerful beep, a little singed, and a map. You follow it through, slowly, hugging the safe line. A smooth, ungainly, entirely sound trip.",
          advance: 5, repair: { astrogation: 6 }, delayDays: 1 }
      ]
    },
    {
      label: "Study it, and then fly through",
      detail: "A day of measurements, and a smaller window.",
      outcomes: [
        { chance: 0.6, text: "The map is lovely, and the crease is, a day later, still there, barely. You thread it with surgeon's care and arrive a long way on with something close to calm.",
          advance: 4, repair: { astrogation: 12 }, delayDays: 1 },
        { chance: 0.4, text: "A day later, the crease has gone. The stars look back at you, flat and unhelpful. The navigator's notes are the best she has ever written, and very little use to anyone.",
          repair: { astrogation: 14, morale: -4 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wonder-009",
  title: "The Surgeon in the Lifeboat",
  category: "wonders",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A lifeboat, tiny, battered, with one occupant who has been waiting a very long time. She is a doctor, sixty-one, smiling and thin and very much alive, and she has with her a full surgical kit, a wry sense of humor, and an offer: 'Take me aboard, and I will make you better at everything you do.'",
  choices: [
    {
      label: "Welcome her aboard",
      cost: { food: -5, water: -4 },
      outcomes: [
        { chance: 0.85, text: "She joins the crew with a handshake and a sandwich. Within a day she has rewritten the sick bay, reorganized the supplies, and taken eleven years off someone's cough. The crew likes her. The crew likes her a great deal.",
          effects: { crew: 1, meds: 5 }, repair: { morale: 14 } },
        { chance: 0.15, text: "She joins the crew and bosses everyone, affectionately, in several languages. The sick bay is spotless. The crew is fed up, and fonder than it will say.",
          effects: { crew: 1, meds: 5 }, repair: { morale: 6 } }
      ]
    },
    {
      label: "Take her kit and her advice, and send her toward the nearest port",
      detail: "She knows where. She's happy to go.",
      outcomes: [
        { chance: 1, text: "She hands over her kit with a grin and a long list of tips. 'Best of luck,' she says. 'Eat more greens.' Her lifeboat accelerates smoothly toward a port you cannot see. The kit is excellent.",
          effects: { meds: 8, parts: 1 }, repair: { morale: 6 } }
      ]
    }
  ]
},
{
  id: "wonder-010",
  title: "The Comet That Rings",
  category: "wonders",
  weight: 0.7,
  zones: ["belt", "outer"],
  text: "A comet crosses your course, and as it passes, it rings. A clean, bright, metallic note. It is not ice. It is a seed of metal, refined and ready, shed by something that works in kilometres. Plates, wire, bearings, all gleaming, tumbling in a loose glittering stream in its wake.",
  choices: [
    {
      label: "Trawl the stream",
      outcomes: [
        { chance: 0.75, text: "A day with the nets out and everyone in suits. By evening the hold is full of perfectly useful, beautifully worked parts, with a faint ring when you tap them. The crew is giddy.",
          effects: { parts: 10 }, repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.25, text: "A day with the nets out. The stream is not as empty as it looks, and a few bits come in at speed. The haul is wonderful, and the hull has a few new dents.",
          effects: { parts: 8 }, damage: { hull: -10 }, repair: { morale: 6 }, delayDays: 1 }
      ]
    },
    {
      label: "Patch the hull with it, directly",
      cost: { power: -4 },
      outcomes: [
        { chance: 1, text: "Plates cut to measure, welded on by hand with a little help from a very willing comet. By the time you are done, the hull has a skin like a mirror. The crew calls it, affectionately, 'the shiny side.'",
          repair: { hull: 35 }, effects: { parts: 2 }, delayDays: 1 }
      ]
    },
    {
      label: "Take a little, and listen to the ring",
      outcomes: [
        { chance: 1, text: "A handful of plates, and an hour of ringing, deep and pure. It is the sound of a place where things are made well. Everybody aboard feels, suddenly, a bit less alone, and a bit better at their jobs.",
          effects: { parts: 3 }, repair: { morale: 16, engines: 8, generator: 8 } }
      ]
    }
  ]
},
{
  id: "wonder-011",
  title: "The Lamplighters",
  category: "wonders",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "They are very small and very bright, and there are thousands of them, like a swarm of fireflies that have learned to do tasks. They pour over the hull, through the seams, into the vents, and where they have been, dead lights come back on. They are, as far as anyone can tell, doing it for fun.",
  choices: [
    {
      label: "Let them work",
      outcomes: [
        { chance: 0.85, text: "A tide of tiny lights in the ducts and conduits. A hundred small dead things wake up. Sensors, nav-lights, the odd cabin lamp, a whole alarm panel that has been out since Earth. They leave in a long, glowing, laughing ribbon, and the ship is brighter than it has been in months.",
          repair: { sensors: 30, astrogation: 20, generator: 10, morale: 10 }, delayDays: 1 },
        { chance: 0.15, text: "A tide of tiny lights in the ducts and conduits, and a few of them stay behind, in the galley, on the ceiling, for the rest of the trip. Nobody can bring themselves to evict them.",
          repair: { sensors: 30, astrogation: 20, morale: 14 }, delayDays: 1 }
      ]
    },
    {
      label: "Feed them",
      detail: "A little power in return. They're so small.",
      cost: { power: -8 },
      outcomes: [
        { chance: 1, text: "A neat, glowing bargain: a little of your power, a lot of theirs. They twinkle in what you suspect is gratitude. Every system on the ship hums a little brighter, and the batteries, when they are done, are fuller than when they started.",
          repair: { sensors: 20, generator: 20, engines: 12, lifeSupport: 12 }, effects: { power: 14 } }
      ]
    }
  ]
},
{
  id: "wonder-012",
  title: "Still Out There",
  category: "wonders",
  weight: 0.35,
  zones: ["outer", "deep"],
  requires: { minDay: 40 },
  text: "A signal on a band nobody has used in ten years: faint, ragged, human. 'This is the Hesperus Colony. Repeat, Hesperus. We are alive. We are building. We saw your drive flare. Keep going. We are coming to meet you.' A date, a place, a name. Then a child's voice, laughing, very close to the microphone, and static.",
  choices: [
    {
      label: "Answer them",
      cost: { power: -6 },
      outcomes: [
        { chance: 1, text: "Eleven minutes of crackle, shouting, tears, and a thousand years of stories. They are alive. They are real. They are in the dark, somewhere ahead, with a light on. The crew stays at the console until the carrier fades, and does not sleep, and does not need to.",
          repair: { morale: 40, astrogation: 8 }, surveyBonus: 0.1, effects: { fuel: 6 } }
      ]
    },
    {
      label: "Log it quietly and keep it for the dark days",
      detail: "Save it. It will be needed.",
      outcomes: [
        { chance: 1, text: "You tell the crew at once, and then you do not talk about it much. The recording lives in a drawer on the bridge. Every bad night it is played, once, softly. It never fails to help.",
          repair: { morale: 28 }, surveyBonus: 0.05 }
      ]
    }
  ]
}
);
