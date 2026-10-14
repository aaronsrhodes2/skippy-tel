/* ORION TRAIL — crew moments. Prefix: pers- .
   Every scenario here features a named crew member via the {crew} token ({crew2} = a second one).
   If an outcome kills crew, it kills {crew}. Needs more than one person aboard. */
SCENARIOS.push(
{
  id: "pers-001",
  title: "{crew}'s Birthday",
  category: "personal",
  text: "{crew} has a birthday. Nobody knows how, on a calendar Earth no longer maintains, but it is today, and {crew} has mentioned it three times. The galley is watching you the way a dog watches a sandwich.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Throw a party",
      detail: "Paste with candles. Candles are a rumor.",
      cost: { food: -8, water: -3 },
      outcomes: [
        { chance: 0.8, text: "Someone sings. Someone else knows the harmony. For forty minutes the ship forgets to be a coffin. {crew} cries into the paste and calls it the best meal of their life.",
          repair: { morale: 14 } },
        { chance: 0.2, text: "The toast goes long, then sideways. By the third verse {crew} is being roasted for sins that predate the Collapse. Someone gets a bruise. Everyone gets a story.",
          repair: { morale: 4 }, effects: { meds: -1 } }
      ]
    },
    {
      label: "A day off, no party",
      detail: "Sleep is also a gift.",
      outcomes: [
        { chance: 0.85, text: "{crew} sleeps fourteen hours and wakes up human. The rest of the crew works around the gap in the roster without comment, which is its own kind of present.",
          repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.15, text: "{crew} sleeps through their own day off and the next watch. Nothing bad happens. It is somehow worse.",
          repair: { morale: 2 }, delayDays: 2 }
      ]
    }
  ]
},
{
  id: "pers-002",
  title: "The Scrubber Whisperer",
  category: "personal",
  text: "{crew} has been talking to the number-two scrubber for two days. Low voice, patient pauses. The scrubber has started answering in a rhythmic clank. {crew} says it is 'just frightened'. Your air has been breathing a little oddly since.",
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Let {crew} keep talking",
      detail: "Science has had worse suggestions.",
      outcomes: [
        { chance: 0.55, text: "The scrubber settles into a quieter pitch and, for no reason anyone can document, runs better. {crew} gets a free week of dessert. Nobody puts this in the maintenance log.",
          repair: { lifeSupport: 10, morale: 4 } },
        { chance: 0.45, text: "It was not frightened. It was failing. By the time the clanking becomes a scream, a seal has already gone. {crew} apologizes to the machine.",
          damage: { lifeSupport: -15 }, effects: { oxygen: -8 } }
      ]
    },
    {
      label: "Pull {crew} off the scrubber",
      detail: "Reassign, quietly.",
      cost: { meds: -1 },
      outcomes: [
        { chance: 0.8, text: "A sedative, a clipboard, a gentle chat about sleep schedules. {crew} nods and is fine. The scrubber goes on exactly as before, a little smug.",
          repair: { morale: 2 } },
        { chance: 0.2, text: "{crew} takes it personally. The scrubber, loyal, goes silent for an hour. The air turns thin and then thick, and then thin.",
          damage: { morale: -8, lifeSupport: -8 } }
      ]
    },
    {
      label: "Have an engineer look it over properly",
      requires: { resources: { parts: 2 } },
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.9, text: "A cracked intake fitting. Replaced. The scrubber's mood improves visibly. {crew} says the machine would like to thank you. You decline the thanks. You do not decline the clean air.",
          repair: { lifeSupport: 15 }, delayDays: 1 },
        { chance: 0.1, text: "The fitting was fine. The problem was an inspection light left on inside the unit, burning a hole in the filter. You are out two parts and a bit of face.",
          repair: { lifeSupport: 5 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "pers-003",
  title: "Homesick",
  category: "personal",
  text: "{crew} has been staring at the last Earth photo for nine hours. It is a parking lot. {crew} keeps describing the parking lot. The details are very good. The details are getting worse for everyone who has to hear them.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Sit with {crew}",
      detail: "Your time is the cheapest thing you have, and also the most limited.",
      outcomes: [
        { chance: 0.7, text: "You listen to the whole parking lot. A blue sedan. A broken cart return. By the end, {crew} laughs once, hard, like a door coming off a hinge, and then sleeps.",
          repair: { morale: 10 }, delayDays: 1 },
        { chance: 0.3, text: "You sit. It does not help. You both end up looking at the photo, and now the parking lot is yours too.",
          damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Medical help",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.85, text: "Chemistry smooths the edges. {crew} returns to duty, blank but functional, with a small private smile, as if told a secret about parking.",
          repair: { morale: 8 } },
        { chance: 0.15, text: "The dose is wrong. {crew} sleeps for a day and a half, and wakes up angry at everyone who let them.",
          damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Put {crew} to work",
      detail: "Grief is a lot easier with a wrench in it.",
      outcomes: [
        { chance: 0.6, text: "{crew} throws themselves into a backlog of repairs nobody had the heart to assign. Two systems come out better. {crew} comes out quieter, which you note, and decide to be fine with.",
          repair: { generator: 6, hull: 6 }, damage: { morale: -4 } },
        { chance: 0.4, text: "Busy hands are not enough. {crew} makes a mistake with a torch that is expensive in parts and nearly in fingers.",
          effects: { parts: -2 }, damage: { morale: -6, hull: -5 } }
      ]
    }
  ]
},
{
  id: "pers-004",
  title: "The Hoard",
  category: "personal",
  text: "A locker comes loose in a burn. Out spill forty ration bars, a flask of something amber, and a note: 'FOR WHEN IT GETS REALLY BAD.' {crew} stands over the mess, quietly dying inside. The ration board has been very confused for weeks.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Confiscate it into the common stores",
      outcomes: [
        { chance: 0.9, text: "Food goes in the bin. The flask is declared medical. {crew} says nothing for three days, which is its own review.",
          effects: { food: 14, meds: 1 }, damage: { morale: -10 } },
        { chance: 0.1, text: "Half of it was already spoiled. You get the other half, and an enemy for life.",
          effects: { food: 6 }, damage: { morale: -14 } }
      ]
    },
    {
      label: "Pardon it and share it out",
      detail: "Mercy has a cost. It also has a flavor, which is chocolate.",
      outcomes: [
        { chance: 0.75, text: "The ship eats like a family for one night. {crew} is, for once, a hero. The hoard is gone by dawn. So is the shame.",
          effects: { food: 6 }, repair: { morale: 10 } },
        { chance: 0.25, text: "Some of the crew say they should have been told. Some say the flask should have been theirs. There is a very quiet argument in the corridor.",
          effects: { food: 6 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Fine {crew} — a week of dishes",
      detail: "Justice, with a sponge.",
      outcomes: [
        { chance: 1, text: "{crew} scrubs the galley with the dignity of a deposed monarch. The food goes into the stores. A hush of satisfaction settles on the cleaner crew.",
          effects: { food: 10 }, damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "pers-005",
  title: "High Stakes, Low Stakes",
  category: "personal",
  text: "{crew} has organized a game of cards in the aft bay. The stakes are ration slips. {crew2} is up four days of food, grinning like a man who has never been caught. The bay is louder than it has any right to be.",
  requires: { resourceAbove: { crew: 2 } },
  choices: [
    {
      label: "Join the game",
      detail: "You are, after all, in command of the odds.",
      outcomes: [
        { chance: 0.5, text: "You win the pot with an ace nobody saw coming. The crew calls it favoritism, then calls for a rematch. Food goes up, dignity goes sideways.",
          effects: { food: 10 }, repair: { morale: 6 } },
        { chance: 0.5, text: "{crew2} cleans you out to the last slip. The room roars. You hand over the food with the grace of a hostage.",
          effects: { food: -14 }, repair: { morale: 6 } }
      ]
    },
    {
      label: "Shut it down",
      detail: "Cards are cards. Rations are rations.",
      outcomes: [
        { chance: 0.9, text: "The game dissolves in a mutter. Rations are returned in a silence you can lean on. The aft bay remembers this.",
          damage: { morale: -8 } },
        { chance: 0.1, text: "The game moves to a locker nobody can find. The rations go with it. Somehow you are out two days of food on the ledger.",
          damage: { morale: -8 }, effects: { food: -6 } }
      ]
    },
    {
      label: "Referee it",
      detail: "Count the pots, break the cheats. A short, lawful session.",
      outcomes: [
        { chance: 0.7, text: "You catch {crew2} dealing from the bottom. Rations are redistributed. The crew thinks you are a god, or at least a minor judge.",
          repair: { morale: 7 }, delayDays: 1 },
        { chance: 0.3, text: "You catch no one, and everyone feels accused. The game ends at a polite, hostile silence.",
          damage: { morale: -4 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "pers-006",
  title: "The Old Injury",
  category: "personal",
  text: "{crew} is moving like a man made of glass. There is an old injury, from before the Collapse, that the cold and the recoil of the burns have found. {crew} insists it is nothing. {crew} is gripping the rail with both hands and the rail is losing.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Treat it properly",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.85, text: "A patch, a brace, a talking-to. {crew} is back on duty in a day, and standing a little taller, like a man whose boss finally noticed.",
          repair: { morale: 6 } },
        { chance: 0.15, text: "It is deeper than it looked. The meds only dull it. {crew} stays on duty and smiles at the wrong moments.",
          damage: { morale: -5 } }
      ]
    },
    {
      label: "Let {crew} push through",
      detail: "Respect the stubbornness. Hope the bone agrees.",
      outcomes: [
        { chance: 0.65, text: "By evening {crew} is walking normally, and by midnight pretending they never limped. The kind of lie that holds the whole ship up.",
          repair: { morale: 2 } },
        { chance: 0.35, text: "{crew} collapses on a ladder. It is worse than it needed to be. You pay for it in meds, in a day, and in the way the crew looks at you.",
          effects: { meds: -3 }, damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Reduce {crew}'s duties",
      detail: "Light duty. The others pick up the slack.",
      outcomes: [
        { chance: 1, text: "Duty shifts around the injury like water. {crew} takes the quiet jobs and hates every minute. The others work a little harder and say nothing, which is the most generous thing a crew can do.",
          damage: { engines: -5 }, repair: { morale: 3 } }
      ]
    }
  ]
},
{
  id: "pers-007",
  title: "Not to Spec",
  category: "personal",
  text: "{crew} has rebuilt a failing coil out of scrap, tape, and a prosthetic hand. It is not to specification. It is not to any specification. It is holding, and humming a note that makes the lights lean toward it.",
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Let it ride",
      detail: "The universe loves a hack.",
      outcomes: [
        { chance: 0.6, text: "It outperforms the original. By a lot. The generator purrs. {crew} refuses to explain it, on the grounds that explaining it might wake it up.",
          repair: { generator: 15, morale: 5 } },
        { chance: 0.4, text: "At ship's midnight the coil does something the manual does not describe. It is not a fire. It is also not not a fire.",
          damage: { generator: -15 }, effects: { parts: -2 } }
      ]
    },
    {
      label: "Replace it with a proper coil",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.95, text: "The spare goes in clean. The hack goes in a drawer, where {crew} visits it like a pet. The generator reads as nominal, which is all it ever asks of you.",
          repair: { generator: 10 }, damage: { morale: -4 } },
        { chance: 0.05, text: "The spare coil is DOA. The hack, wounded in its honor, picks up the slack. {crew} says nothing. {crew} does not have to.",
          repair: { generator: 6 } }
      ]
    },
    {
      label: "Take the hack apart to learn how it works",
      outcomes: [
        { chance: 0.5, text: "It's brilliant, and now it's in your manual. {crew} is quietly furious and also quietly proud. You take notes the whole time.",
          repair: { generator: 8 }, damage: { morale: -3 } },
        { chance: 0.5, text: "It comes apart into six pieces and one that you cannot identify. {crew} tells you in detail what you have done.",
          damage: { generator: -8, morale: -6 }, effects: { parts: -1 } }
      ]
    }
  ]
},
{
  id: "pers-008",
  title: "A Letter Nobody Can Deliver",
  category: "personal",
  text: "{crew} asks to send a message to Earth. It would take a burst of power and cross a void with nobody on the other side. 'I know,' says {crew}. 'I know.' The request has been written out and signed. It is one sentence long.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Send it",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.9, text: "The transmitter spits it into the dark. {crew} watches the status light until it clears, then goes to sleep without being asked. The whole crew sleeps better, which they will deny.",
          repair: { morale: 11 } },
        { chance: 0.1, text: "The transmitter fails mid-burst. {crew} takes it with a nod that is worse than tears. Power is gone, and the sentence is half out.",
          damage: { morale: -3, sensors: -5 } }
      ]
    },
    {
      label: "Keep the log, don't transmit",
      detail: "Save the power. Save the sentence.",
      outcomes: [
        { chance: 0.7, text: "You file the letter in the ship's record, where it will be read by someone, someday, probably. {crew} accepts it with a small bow. The crew says nothing. The crew sees everything.",
          damage: { morale: -5 } },
        { chance: 0.3, text: "{crew} does not accept it. They sit in the dark by the transmitter for a long time. By morning they are quiet, and so is everyone else.",
          damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "pers-009",
  title: "The Thermostat War",
  category: "personal",
  text: "{crew} and {crew2} have not spoken in four days. The cause is the thermostat. The thermostat is a dial with three settings. It has been set to two by {crew} and three by {crew2} every hour on the hour since the Moon. Tempers are warmer than the cabin.",
  requires: { resourceAbove: { crew: 2 } },
  choices: [
    {
      label: "Mediate",
      detail: "A day of diplomacy. It's a long day.",
      outcomes: [
        { chance: 0.65, text: "You find the compromise: a half-setting nobody had thought to consider. They shake hands. {crew2} gives {crew} a blanket. A treaty is signed with a blanket.",
          repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.35, text: "Mediation goes well until {crew2} says something about {crew}'s mother. The thermostat wins, as thermostats do.",
          damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Lock it at the setting you prefer",
      detail: "Rule by decree. It is as warm as you like it.",
      outcomes: [
        { chance: 0.7, text: "Order is restored. The dial is covered in tape. Both {crew} and {crew2} resent you equally, which the ship records as unity.",
          damage: { morale: -6 }, effects: { power: 5 } },
        { chance: 0.3, text: "One of them rips the tape off in the night and the dial overshoots. The ship runs warm. Water goes up the vents.",
          damage: { morale: -8 }, effects: { water: -6 } }
      ]
    },
    {
      label: "Let it burn out",
      detail: "Adults, one assumes.",
      outcomes: [
        { chance: 0.4, text: "They sort it out by themselves in a way that involves the word 'sweater'. Neither mentions it again.",
          repair: { morale: 3 } },
        { chance: 0.6, text: "They do not. The feud spreads to other dials. Several lights are now at war over which of them is the real brightness.",
          damage: { morale: -10 }, effects: { power: -4 } }
      ]
    }
  ]
},
{
  id: "pers-010",
  title: "Third Watch, Someone in the Corridor",
  category: "personal",
  text: "{crew} reports someone in the aft corridor at 0300. Footsteps, a shadow, a nod. Nobody else saw. Sensors log 'nothing' in a formatting you do not recall ever seeing. {crew} is not the type. That is the problem.",
  zones: ["outer", "deep"],
  requires: { resourceAbove: { crew: 2 } },
  choices: [
    {
      label: "Investigate with {crew2}",
      detail: "Torches, a clipboard, a bad feeling.",
      outcomes: [
        { chance: 0.5, text: "A loose panel, a loop of cable swinging, a reflection from a poorly sealed port. A thing, not a person. You hold the cable like a dead snake and the crew laughs for a minute.",
          repair: { morale: 4 }, delayDays: 1 },
        { chance: 0.3, text: "A stowaway rat, three months aboard, eating the emergency rations in the aft bay. It looks at you. You look at it. It has the confidence of a lieutenant.",
          effects: { food: -6 }, repair: { morale: 6 }, delayDays: 1 },
        { chance: 0.2, text: "You find nothing, and the nothing was wrong in a way that costs you a night's sleep and a day of sensors.",
          damage: { morale: -8, sensors: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "Log it and move on",
      outcomes: [
        { chance: 0.75, text: "Nothing happens. A night goes by. And another. {crew} still takes the long way round the aft corridor.",
          damage: { morale: -3 } },
        { chance: 0.25, text: "It happens again. Then to someone else. By the end of the week, nobody walks the aft corridor alone, which slows every repair on the ship.",
          damage: { morale: -8, engines: -5 } }
      ]
    },
    {
      label: "Seal the corridor",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 1, text: "A weld, a sign, a hush. Nobody asks what is behind the door. It is a little cooler in the ship afterward, and one fewer room for things to be in.",
          damage: { morale: -3 } }
      ]
    }
  ]
}
);
