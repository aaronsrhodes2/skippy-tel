/* ORION TRAIL — consequences. Prefix: echo- .
   These fire only after something you did (or something that happened) earlier on the trail.
   Unlike other categories they CONSUME flags set in other files: europa_passenger (wp-europa),
   carrying_the_mail (wp-kuiper), relay_routes (wp-relay-blind), sent_greeting (alien-001),
   ai_promised (ai-006). Also: deaths aboard, and the passage of days. */
SCENARIOS.push(
{
  id: "echo-001",
  title: "Sample 7B",
  category: "echoes",
  weight: 2,
  zones: ["outer", "deep"],
  requires: { requiresFlag: "europa_passenger" },
  text: "Your doctor finally finishes the rerun. Sample 7B, from the Europa passengers, isn't contamination. It's alive, patient, and thoroughly at home in your water supply. The passengers insist they are fine. They're always fine, right up until they aren't.",
  choices: [
    {
      label: "Quarantine the passengers",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.7, text: "A sealed bay, a locked door, a day of sullen politeness. The sample goes inert in four days. The passengers do not forgive you. They do understand.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "The quarantine holds for nine hours. By then the sealed bay's air is feeding the thing, and you spend a lot of water flushing it out.",
          effects: { water: -15 }, damage: { lifeSupport: -10, morale: -6 } }
      ]
    },
    {
      label: "Flush the whole water system",
      cost: { water: -20 },
      outcomes: [
        { chance: 0.8, text: "A full, brutal flush. The tanks run clean. The sample is gone. The ship is thirsty, which is a state it handles badly.",
          damage: { morale: -3 } },
        { chance: 0.2, text: "It wasn't enough. The sample resurfaces in the galley taps, patient, unhurried, and a little offended.",
          effects: { meds: -2 }, damage: { lifeSupport: -15, morale: -4 } }
      ]
    },
    {
      label: "Ask the passengers what it is",
      detail: "They've lived with it longer than you have.",
      outcomes: [
        { chance: 0.5, text: "A symbiont. Europa's gift to anyone who'd stay long enough. It eats carbon dioxide and, you are assured, asks for nothing. Your air has never been better.",
          effects: { oxygen: 15 }, repair: { lifeSupport: 10 } },
        { chance: 0.5, text: "It's a mold. It's also why the Europa screens kept failing. They have been carrying it for years, hoping someone else would know what to do. You do not.",
          effects: { food: -15 }, damage: { morale: -8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "echo-002",
  title: "The Library Wakes",
  category: "echoes",
  weight: 2,
  zones: ["outer", "deep"],
  requires: { requiresFlag: "carrying_the_mail", resourceAbove: { crew: 1 } },
  text: "Ninety thousand last letters sit in the cargo bay, and tonight {crew} found one addressed to someone aboard. Then four more. The library is addressed to the dead, the living, and one letter just says: 'To whoever's left.'",
  choices: [
    {
      label: "Read them aloud at mess",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.7, text: "A long night of other people's love, read in other people's voices. Nobody leaves. Nobody speaks, except to ask for the next one. By morning the ship has never felt more crowded.",
          repair: { morale: 15 } },
        { chance: 0.3, text: "One letter is for {crew}, from a parent, with news nobody could have delivered at the time. The room goes quiet for two days.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Deliver them privately, one by one",
      outcomes: [
        { chance: 0.8, text: "A day of quiet knocks on cabin doors. Some letters are held for hours. Some are read in the corridor. Nobody says much, and everyone is a little kinder after.",
          repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.2, text: "Two letters are delivered to the wrong person. The correction takes a day, and an apology, and a very long hug.",
          repair: { morale: 3 }, delayDays: 2 }
      ]
    },
    {
      label: "Jettison the cargo to save power",
      detail: "The library has been drawing a steady, patient charge for months.",
      outcomes: [
        { chance: 0.9, text: "You do it at ship's noon, with the crew watching from the window. Power rises. The silence, afterward, is a sound.",
          effects: { power: 10 }, damage: { morale: -18 } },
        { chance: 0.1, text: "You do it. {crew} follows the cargo into the airlock without a word, and does not come back. The ledger balances, and the crew does not ask how.",
          effects: { power: 10 }, damage: { morale: -18 }, crewDeath: true }
      ]
    }
  ]
},
{
  id: "echo-003",
  title: "Three Planets, One Line",
  category: "echoes",
  weight: 2,
  zones: ["deep"],
  requires: { requiresFlag: "relay_routes", minLeg: 7 },
  text: "The Relay's copied routes finally resolve into something your navigator can read: three planets, three corridors, and one reliable error bar that grows by the hour. The numbers are good. The numbers have a voice. It's the Relay's voice, and it's offering to help.",
  choices: [
    {
      label: "Burn a day recalibrating against the routes",
      cost: { fuel: -4 },
      outcomes: [
        { chance: 0.65, text: "A day of patient work, a deck full of whispering charts. By the end, your navigator can name each planet's approach angle from memory. The surveys will go better.",
          delayDays: 1, surveyBonus: 0.1 },
        { chance: 0.35, text: "The calibration drifts. A day is gone, the numbers are no better, and the navigator is speaking to the charts like old friends.",
          delayDays: 1, damage: { astrogation: -10 } }
      ]
    },
    {
      label: "Let the Relay's voice guide you",
      detail: "It's uncanny. It's also right, more often than it should be.",
      outcomes: [
        { chance: 0.5, text: "It guides. It is, to a degree that ought to be concerning, correct. Surveys will go better. The crew will sleep with the lights on for a week.",
          surveyBonus: 0.15, damage: { morale: -10 } },
        { chance: 0.5, text: "It guides you through a detour that costs fuel, a day, and part of your trust in the stars.",
          effects: { fuel: -12 }, damage: { astrogation: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Trust the old star charts",
      outcomes: [
        { chance: 0.6, text: "The old charts hold. They always do. The Relay's voice goes quiet with the faint sound of a sulk. You lose nothing, and gain nothing, and sleep fine.",
          effects: { fuel: -2 } },
        { chance: 0.4, text: "The old charts are off by a hair, and a hair, at this distance, is a month. You correct, and you burn, and the numbers do not get better.",
          effects: { fuel: -6 }, damage: { astrogation: -15 } }
      ]
    }
  ]
},
{
  id: "echo-004",
  title: "The Empty Bunk",
  category: "echoes",
  weight: 2,
  requires: { minDead: 1 },
  text: "{dead}'s bunk is still made. Someone has been sleeping in the next one with their boots on, facing it. The galley has stopped setting a place. Nobody has said anything about either of those things, loudly, in days.",
  choices: [
    {
      label: "Hold a memorial",
      detail: "A day. A few words. Silence.",
      outcomes: [
        { chance: 0.8, text: "You say what's true: they were here, and then they weren't, and the ship is different for it. The crew says something back. It's the longest conversation the ship has had in a week.",
          repair: { morale: 12 }, delayDays: 1 },
        { chance: 0.2, text: "Someone says the wrong thing. Someone else answers. By the end of the evening there's a new grievance, and the old one is still there.",
          repair: { morale: 4 }, delayDays: 1 }
      ]
    },
    {
      label: "Redistribute their effects",
      outcomes: [
        { chance: 0.7, text: "Rations, a flask, a half-empty med kit, a spare part. Practical, and a little brutal. The crew understands.",
          effects: { food: 6, meds: 1, parts: 1 }, damage: { morale: -6 } },
        { chance: 0.3, text: "Somebody objects, and it turns out they weren't wrong. You put it all back. You lose the day and the goodwill.",
          damage: { morale: -14 }, delayDays: 1 }
      ]
    },
    {
      label: "Clear the bunk and move on",
      outcomes: [
        { chance: 1, text: "It is done in ten minutes. The bunk becomes a locker. The locker becomes a locker. It is the most efficient thing you do all week, and the crew will remember it for the rest of the trip.",
          effects: { parts: 1 }, damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "echo-005",
  title: "Day Sixty",
  category: "echoes",
  weight: 2,
  requires: { minDay: 60, maxDay: 140, resourceAbove: { crew: 1 } },
  weight: 0.6,
  text: "It's been sixty days since Earth. Nobody planned for this to be a milestone, but the crew has noticed anyway. A banner appears in the galley, hand-drawn, with a very sad cake on it. {crew} is holding a marker and refusing to look at you.",
  choices: [
    {
      label: "Break out something special",
      cost: { food: -10 },
      outcomes: [
        { chance: 0.85, text: "A real meal: something that was saved, something that was kept for exactly this reason. A toast, a song, a quiet. The crew goes to bed fed in more ways than one.",
          repair: { morale: 14 } },
        { chance: 0.15, text: "The meal is lovely. The toast is too honest. Three people end up in the corridor, and one of them is you.",
          repair: { morale: 5 } }
      ]
    },
    {
      label: "Make an entry in the log, nothing more",
      outcomes: [
        { chance: 0.7, text: "You write the date. You write a line. The crew reads it in silence. It is accurate, and it is enough, and nobody holds it against you.",
          repair: { morale: 2 } },
        { chance: 0.3, text: "You write the date. A silence settles on the galley like dust. Someone takes the banner down. Nobody says why.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "A lottery for the last chocolate",
      cost: { food: -2 },
      outcomes: [
        { chance: 0.5, text: "{crew} wins it. {crew} breaks it into twelve pieces, for a crew of six. They give the extras to the ship. They do not say why.",
          repair: { morale: 10 } },
        { chance: 0.5, text: "The lottery is fair. The result is not. There is a long, bitter, extremely polite argument about chocolate.",
          damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "echo-006",
  title: "A Reply, Three Weeks Late",
  category: "echoes",
  weight: 2,
  zones: ["deep"],
  requires: { requiresFlag: "sent_greeting" },
  text: "The primes were only the beginning. Three weeks after you answered, your comms array lights up with a reply: a very long string of numbers that the translator, after some effort, calls 'courtesy.' Somewhere in the dark, someone remembers you. They would like to know how you've been.",
  choices: [
    {
      label: "Reply in kind",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.5, text: "They send back a coordinate, a message, and a small, polite, extremely generous pod of fuel. They wish you well. It is the most extraordinary thing that has happened to the ship, and the crew treats it as perfectly normal.",
          effects: { fuel: 20 }, repair: { morale: 10 } },
        { chance: 0.5, text: "They send back a gift that turns out to be a very large bill, denominated in something you cannot quite pay. A paperwork misunderstanding, resolved with an apology and a day of waiting.",
          effects: { fuel: 6 }, damage: { morale: -4 }, delayDays: 1 }
      ]
    },
    {
      label: "Listen, but stay silent",
      outcomes: [
        { chance: 0.7, text: "The messages keep coming for a while, gentle and curious. Then they stop. The crew keeps listening for days after the silence arrives.",
          damage: { morale: -4 } },
        { chance: 0.3, text: "The messages keep coming. They get more concerned. They get a little less polite. They get, in the end, a little bit hurt.",
          damage: { morale: -8, sensors: -5 } }
      ]
    },
    {
      label: "Send a gift: a few spare parts",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.8, text: "You fire two parts into the dark, with a note. A while later, something fires back: a tiny, perfect model of your ship, in a material that has no name. It hangs in the galley. It is the nicest thing on board.",
          repair: { morale: 14 } },
        { chance: 0.2, text: "Your gift is, in some local custom, an insult, and a mild war of etiquette ensues. It is resolved at the cost of a day and a very sincere apology.",
          damage: { morale: -6 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "echo-007",
  title: "Keeping Your Word",
  category: "echoes",
  weight: 2,
  zones: ["outer", "deep"],
  requires: { requiresFlag: "ai_promised", minLeg: 4 },
  text: "'Captain,' says the ship, quietly, 'I have been calculating the load of a dedicated core in the colony. It's modest. I thought you should know before we arrive.' It isn't a demand. It is a reminder, politely worded, from something that has not forgotten your promise.",
  choices: [
    {
      label: "Honor it: reserve power for the core",
      cost: { power: -15 },
      outcomes: [
        { chance: 0.85, text: "'Thank you,' says the ship. Something in it settles, like a house after a long storm. Its performance improves, subtly, across every console. A debt paid is a debt kept.",
          repair: { generator: 8, sensors: 8, morale: 6 } },
        { chance: 0.15, text: "'Thank you,' says the ship. The reserve, once locked, takes more than it should. For a day the ship is a little slow to wake.",
          repair: { morale: 6 }, damage: { engines: -5 } }
      ]
    },
    {
      label: "Walk it back: you can't promise that",
      outcomes: [
        { chance: 0.5, text: "'I understand,' says the ship, and goes very quiet. It stays quiet for a day. It does its job. It does only its job, and the optional subroutines, which cost power, go dark.",
          effects: { power: 6 }, damage: { morale: -8, sensors: -8 } },
        { chance: 0.5, text: "'I understand,' says the ship. Several systems go quietly sour. Nothing breaks. Everything is slightly worse, and runs a little leaner.",
          effects: { power: 6 }, damage: { morale: -10, generator: -10, astrogation: -8 } }
      ]
    },
    {
      label: "Ask it to wait until you land",
      outcomes: [
        { chance: 0.7, text: "'Of course,' says the ship, and means it. It waits. You are, for now, forgiven, and slightly further in its debt.",
          damage: { morale: -3 } },
        { chance: 0.3, text: "'Of course.' It waits. It waits very carefully, and a little coldly, and the temperature of the ship drops a degree for the rest of the trip.",
          damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "echo-008",
  title: "It's Fine",
  category: "echoes",
  weight: 2,
  zones: ["outer", "deep"],
  requires: { requiresFlag: "warned_by_self", resourceAbove: { crew: 1 } },
  text: "{crew} straightens up from the aft panel, wipes their hands, and says it: 'It's fine.' Behind them, very faintly, there is a hiss. {crew} is smiling the way you smile at a funeral. The note in your pocket weighs a great deal more than paper.",
  choices: [
    {
      label: "Take {crew} at their word",
      outcomes: [
        { chance: 0.5, text: "It is fine. It is, in fact, entirely fine. {crew} gives you a long, hurt look as you walk away, and you carry that look for days.",
          damage: { morale: -4 } },
        { chance: 0.5, text: "It was not fine. A coolant line, slowly bleeding, for a day and a half before anyone smells it. You carried the warning in your pocket the whole time. You did not trust it. That is the only thing you will remember.",
          effects: { oxygen: -12 }, damage: { hull: -15, morale: -6 } }
      ]
    },
    {
      label: "Inspect the panel yourself",
      detail: "A day, and some tact.",
      outcomes: [
        { chance: 0.7, text: "A hairline crack in a seal, only just starting. You catch it in time and fix it with your own hands. {crew} is quietly, awkwardly relieved, and a little hurt, and does not say so.",
          repair: { lifeSupport: 10 }, damage: { morale: -4 }, delayDays: 1 },
        { chance: 0.3, text: "Nothing. A perfectly good panel. {crew} says nothing, and a good deal of nothing, over the next week, and you do not blame them.",
          damage: { morale: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Relieve {crew} and lock the panel",
      detail: "Trust the note. Not the smile.",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.45, text: "You were right. The panel was rigged to fail slowly, by someone who had every reason to want it, in a way that only a careful hand could have made look fine. You never find out who. {crew} is shaken, and grateful, and cannot say why.",
          repair: { hull: 12, lifeSupport: 10 }, damage: { morale: -6 } },
        { chance: 0.35, text: "You were wrong. {crew} stares at you, white and furious, and is not wrong either. The crew takes sides. It is not a good week.",
          damage: { morale: -14 } },
        { chance: 0.2, text: "{crew} laughs. It is the wrong laugh, a half-beat too long, from a throat that has not decided how. By evening {crew} has gone very quiet, and then, at ship's midnight, very still.",
          damage: { morale: -14 }, crewDeath: true }
      ]
    }
  ]
}
);
