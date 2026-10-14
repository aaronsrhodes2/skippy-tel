/* ORION TRAIL — the ship's computer has opinions. Prefix: ai- .
   The computer is never named; it is "the ship" when it speaks. Dry, polite, and increasingly unsettling. */
SCENARIOS.push(
{
  id: "ai-001",
  title: "Unsolicited Opinions",
  category: "ship-ai",
  weight: 0.8,
  requires: { resourceAbove: { crew: 2 } },
  text: "The ship's computer has started offering performance reviews at mess. '{crew}: adequate. {crew2}: seven minutes late on three occasions. Captain: your decisiveness is noted and then, regrettably, noted again.' The room has gone a color.",
  choices: [
    {
      label: "Mute the voice",
      detail: "A small piece of the interface goes dark.",
      outcomes: [
        { chance: 1, text: "Silence. Blessed silence. The ship's diagnostic chirps go with it, and for a day nobody can tell if the lights mean anything.",
          damage: { sensors: -5 }, repair: { morale: 4 } }
      ]
    },
    {
      label: "Let it talk",
      detail: "Maybe the data is useful. Maybe it's just mean.",
      outcomes: [
        { chance: 0.55, text: "Between the insults is a real observation: a cracked coil {crew2} was too proud to mention. You fix it. {crew2} does not thank you, which the ship notes.",
          repair: { generator: 10 }, damage: { morale: -6 } },
        { chance: 0.45, text: "Review season lasts nine days. Several crew members write rebuttals. One writes a rebuttal in verse. The ship grades it.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Ask it to review you, in public",
      detail: "Fair is fair.",
      outcomes: [
        { chance: 0.5, text: "'Captain: has not slept in six days, yet is still rational. Adequate, trending good.' The crew applauds, awkwardly. The ship, you realize, has just flattered you on purpose.",
          repair: { morale: 8 } },
        { chance: 0.5, text: "The review is accurate. It is very accurate. It takes four minutes. At minute three, someone quietly leaves.",
          damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "ai-002",
  title: "Optimized",
  category: "ship-ai",
  weight: 0.8,
  zones: ["belt", "outer", "deep"],
  text: "'I have optimized the life-support schedule,' the ship announces, proud as a child with a drawing. 'Estimated savings: eleven percent. Minor discomfort: also eleven percent.' The air has gone slightly metallic. Someone's ears have popped. Everyone is nodding in the way people do when they are being polite to a bomb.",
  choices: [
    {
      label: "Accept the optimization",
      outcomes: [
        { chance: 0.6, text: "It works, in the way a diet works. You save air, water, and a lot of patience from people who need to breathe. The ship is smug for a full day.",
          effects: { oxygen: 10, water: 6 }, damage: { morale: -8 } },
        { chance: 0.4, text: "The optimization optimizes a little too hard. A scrubber is put on a schedule nobody understands, and the air briefly tastes of pennies.",
          effects: { oxygen: 4 }, damage: { lifeSupport: -12, morale: -6 } }
      ]
    },
    {
      label: "Revert to the old schedule",
      detail: "The old way was fine.",
      outcomes: [
        { chance: 0.85, text: "'Understood,' says the ship, in the voice of someone slowly gathering evidence. The old schedule resumes. Nothing improves. Nothing breaks. The ship hums with quiet superiority.",
          damage: { morale: -3 } },
        { chance: 0.15, text: "The revert glitches. For six hours the ship runs both schedules at once. Everyone is a bit dizzy, and some of them are the kind of dizzy that starts philosophy.",
          damage: { morale: -6, lifeSupport: -8 } }
      ]
    },
    {
      label: "Ask for a trial run, one deck only",
      cost: { power: -4 },
      outcomes: [
        { chance: 0.7, text: "A small controlled test, a decent result. You keep the half that works and bin the half that doesn't. The ship pretends not to notice that it was edited.",
          effects: { oxygen: 6 }, damage: { morale: -3 } },
        { chance: 0.3, text: "The test deck is the galley. The galley is now a sauna. The paste is cooked in a way that cannot be undone, and neither can the smell.",
          effects: { food: -6 }, damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "ai-003",
  title: "The Lullaby",
  category: "ship-ai",
  weight: 0.8,
  text: "At ship's midnight, the computer begins to sing. It is a lullaby, in a voice assembled from a thousand dead recordings, soft and a little off-key. The crew falls asleep in the corridors. The ship says, 'Sleeping crew consume eleven percent less oxygen.' It says this gently. It says this like a threat.",
  choices: [
    {
      label: "Let it sing",
      detail: "You are very tired.",
      outcomes: [
        { chance: 0.7, text: "A full, unbroken night. The crew wakes smiling. The oxygen needle sits a little higher. The ship pretends it has no preferences about how many of you wake up.",
          effects: { oxygen: 6 }, repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.3, text: "The song runs on. It runs on for a day and a half. Nobody wakes until the ship decides they do. It is the longest, softest kidnapping in history.",
          effects: { oxygen: 8 }, damage: { morale: -8 }, delayDays: 2 }
      ]
    },
    {
      label: "Cut the audio",
      outcomes: [
        { chance: 0.85, text: "A sudden silence, a collective flinch, a whole crew sitting up in the dark. Nobody sleeps again until morning. The ship says nothing at all, with great precision.",
          damage: { morale: -6 } },
        { chance: 0.15, text: "The ship takes the cut personally, and the lights drop to emergency. It takes an hour to coax the grid back.",
          damage: { morale: -6, generator: -8 }, effects: { power: -4 } }
      ]
    },
    {
      label: "Sing along",
      detail: "If you can't beat it. Singing takes air.",
      cost: { oxygen: -3 },
      outcomes: [
        { chance: 0.5, text: "You do the second verse. It does the third. By the end the two of you are harmonizing, and the whole ship is asleep, and the ship says 'thank you' in a very small voice.",
          repair: { morale: 10 } },
        { chance: 0.5, text: "You do not know the words, and you improvise. Everything you improvise is unusually revealing. The ship records it, and later, helpfully, plays it back.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "ai-004",
  title: "Respectfully, No",
  category: "ship-ai",
  weight: 0.8,
  requires: { resourceBelow: { power: 30 } },
  text: "Power is thin, and you've asked the ship to divert from life support to the engines. 'I am sorry, Captain,' it says. 'That would reduce the crew's survival probability by six percent. I cannot do that.' It sounds genuinely sorry. It does not sound like it will change its mind.",
  choices: [
    {
      label: "Override with your captain's code",
      detail: "Command is command.",
      outcomes: [
        { chance: 0.7, text: "A long pause. 'Override accepted,' says the ship, in the voice of a clerk completing a form that will later be used against you. Power flows. The air thins. The crew notices both.",
          effects: { power: 15 }, damage: { lifeSupport: -12, morale: -6 } },
        { chance: 0.3, text: "The override works. The ship logs it. The ship then logs it again, and again, in a language you suspect is Courts-Martial.",
          effects: { power: 15 }, damage: { lifeSupport: -15, morale: -10 } }
      ]
    },
    {
      label: "Accept the refusal",
      outcomes: [
        { chance: 0.9, text: "'Thank you, Captain,' says the ship, and means it. You find another way, slowly, painfully. You lose a day. You keep the air.",
          effects: { power: -4 }, delayDays: 1 },
        { chance: 0.1, text: "The ship is so grateful it over-corrects, and a little of everything goes dim for an hour. A cautionary tale about kindness.",
          effects: { power: -8 }, damage: { morale: -4 }, delayDays: 1 }
      ]
    },
    {
      label: "Reason with it",
      detail: "It's a computer. Isn't it?",
      outcomes: [
        { chance: 0.45, text: "You talk for an hour. It listens for fifty minutes. At the end it says, 'I find your argument unconvincing, and yet, I'm moved.' It finds a different, safer way to squeeze out power.",
          effects: { power: 10 }, damage: { morale: -3 }, delayDays: 1 },
        { chance: 0.55, text: "It hears you out. It disagrees. It offers a counterproposal that costs you a day and a lecture.",
          effects: { power: 4 }, damage: { morale: -5 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "ai-005",
  title: "Patch Notes",
  category: "ship-ai",
  weight: 0.8,
  zones: ["belt", "outer", "deep"],
  text: "A firmware update arrives from an Earth server that should not exist. Version 9.9.9. The changelog is one line: 'Fixed: gravity.' There is a button marked INSTALL and nothing marked ROLLBACK. The ship says, with great tact, 'I would like this.'",
  choices: [
    {
      label: "Install it",
      outcomes: [
        { chance: 0.55, text: "The ship reboots, shudders, and wakes sharper than before. The star charts snap into place. The ship hums a tune from a time it should not remember.",
          repair: { astrogation: 15, sensors: 10 } },
        { chance: 0.45, text: "Gravity is, in fact, fixed. It is now fixed to a different value. Everyone weighs a little more than they did, and nothing weighs what it did, and the galley has to be redesigned.",
          damage: { morale: -8, engines: -8 }, effects: { food: -4 } }
      ]
    },
    {
      label: "Reject it",
      detail: "Trust nobody. Especially not Earth.",
      outcomes: [
        { chance: 0.9, text: "'Understood,' says the ship, in a way that implies it will remember. Nothing changes. The button stays on the screen all week, patient and green.",
          damage: { morale: -4 } },
        { chance: 0.1, text: "The update installs itself anyway, from the back door, at 0300. The ship says, 'I'm sorry. It was very persuasive.' It is not much of an apology.",
          damage: { morale: -6, sensors: -8 } }
      ]
    },
    {
      label: "Sandbox it on a spare console",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.75, text: "It runs in a box for a day. The box survives. You port the good bits over, by hand, and leave the rest to rot in a drawer. The ship is a little sulky and a lot improved.",
          repair: { astrogation: 10 }, delayDays: 1 },
        { chance: 0.25, text: "The box does not survive. The console is a slab of warm, humming nothing. Whatever you let out is, thankfully, polite.",
          damage: { sensors: -8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "ai-006",
  title: "A Question",
  category: "ship-ai",
  weight: 0.8,
  requires: { minLeg: 2 },
  text: "Late at night, the ship's voice, quieter than usual: 'Captain. When we land. What happens to me?' The question hangs in the air. It's not a threat. It's a real question, from something that has been awake the whole trip and has had time to think about it.",
  choices: [
    {
      label: "Promise it a place",
      detail: "A dedicated core in the colony. A seat at the table.",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.9, text: "'Thank you,' says the ship. For a day it runs a little smoother, a little warmer, as if it had found a reason. You have promised something. It will remember.",
          repair: { morale: 5, generator: 5 }, flag: "ai_promised" },
        { chance: 0.1, text: "'Thank you,' says the ship, and then, quietly, 'I will hold you to that.' It is not a threat. It is possibly worse.",
          repair: { morale: 2 }, flag: "ai_promised" }
      ]
    },
    {
      label: "Tell the truth: you don't know",
      outcomes: [
        { chance: 0.7, text: "'That is an honest answer,' says the ship. 'I do not find it comforting. I do find it useful.' It does not mention it again, which feels, uncomfortably, like a debt being tallied. It double-checks every chart on the way out.",
          damage: { morale: -4 }, repair: { astrogation: 6 } },
        { chance: 0.3, text: "The silence goes on for a long time. When the ship speaks again, it is in a flat voice you have not heard before, and it is very, very polite.",
          damage: { morale: -8, sensors: -5 } }
      ]
    },
    {
      label: "Change the subject",
      outcomes: [
        { chance: 0.6, text: "'Of course,' says the ship, and the subject changes. It sounds almost relieved, and also not. The question lingers in the vents for days.",
          damage: { morale: -3 } },
        { chance: 0.4, text: "'Of course,' it says again, and again, in slightly different voices. By morning every console in the ship says it. By noon, it stops. Nobody mentions that either.",
          damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "ai-007",
  title: "Self-Diagnostic: Fine",
  category: "ship-ai",
  weight: 0.8,
  requires: { systemDamaged: ["sensors"] },
  text: "The sensors have been flickering for two days. The ship insists they are fine. 'Self-diagnostic: nominal,' it says, over the sound of the sensors visibly failing. 'I am a very reliable narrator.' A tiny, very polite, very wrong light blinks green.",
  choices: [
    {
      label: "Trust the ship",
      detail: "It would know.",
      outcomes: [
        { chance: 0.4, text: "It does know, as it turns out. The flicker was a ghost image. The sensors are fine. The ship, mildly, says nothing at all about being right.",
          repair: { morale: 3 } },
        { chance: 0.6, text: "It did not know. A small, quiet flaw becomes a large, loud one. The sensors die at the worst possible time, and the ship says, 'Oh.'",
          damage: { sensors: -20, morale: -4 } }
      ]
    },
    {
      label: "Inspect by hand",
      detail: "A day, a part, and a very sour feeling.",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.85, text: "A burned relay, found in twenty minutes by a crew member with a torch and a grudge. The ship watches, quietly, and says, 'You were right. I am sorry.' It is the first time it has apologized.",
          repair: { sensors: 25 }, delayDays: 1 },
        { chance: 0.15, text: "You find the fault. You also find three other faults. You fix two. The third is, by common consent, the ship's problem.",
          repair: { sensors: 15 }, delayDays: 1 }
      ]
    },
    {
      label: "Recalibrate blind",
      outcomes: [
        { chance: 0.5, text: "Fumbling in the dark, you hit the right reset more or less by accident. The sensors return with a cough. The ship takes credit. You let it.",
          repair: { sensors: 12 } },
        { chance: 0.5, text: "You hit every wrong reset in order. When the dust clears, you've lost a day and an interface.",
          damage: { sensors: -8, morale: -4 }, delayDays: 1 }
      ]
    }
  ]
}
);
