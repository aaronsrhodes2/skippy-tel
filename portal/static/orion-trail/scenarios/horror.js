/* ORION TRAIL — dread. Prefix: dread- .
   Cosmic and psychological horror: things that notice you, wrongness in the ship, the dark having opinions.
   Outer system and deep space only. Weight ~0.6; engine tilts these toward low morale and the deep leg.
   Crew deaths are real here but never better than a 30% branch, and never the obviously-correct choice. */
SCENARIOS.push(
{
  id: "dread-001",
  title: "Knocking",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "Three knocks on the airlock door. Then two. Then three. Outside is vacuum and a very long way. The knocks come from outside the hull. It is the rhythm {crew} uses at a cabin door. {crew} is standing beside you, white, shaking their head.",
  choices: [
    {
      label: "Cycle the airlock",
      detail: "See who is out there.",
      outcomes: [
        { chance: 0.55, text: "Both doors, one cycle, and the lock is empty. Nothing. Frost on the inside of the inner door, in the shape of hands, palm-in, fingers spread, as though pressed from outside. It melts as you watch. The air went with the cycle.",
          effects: { oxygen: -10 }, damage: { morale: -8 } },
        { chance: 0.25, text: "The lock is empty. The ship is very quiet. At ship's midnight, the knocking resumes, now on the inside of {crew}'s cabin door. In the morning, the door is open and the bunk has not been slept in.",
          effects: { oxygen: -10 }, damage: { morale: -12 }, crewDeath: true },
        { chance: 0.2, text: "Empty, but for a single frozen flower on the deck. A real one, white petals, not from any garden you know. The crew passes it from hand to hand and no one can say what it means. They keep it.",
          effects: { oxygen: -10 }, repair: { morale: 8 } }
      ]
    },
    {
      label: "Knock back",
      outcomes: [
        { chance: 0.5, text: "You knock three. After a long pause, one knock answers, soft, almost apologetic. Then the sound recedes along the hull, step by step, toward the aft, and is gone.",
          damage: { morale: -4 } },
        { chance: 0.5, text: "You knock three. It knocks three, back, louder. Again. Again, harder, until the hull rings and a seam gives with a long creak that you feel in your teeth.",
          damage: { hull: -12, morale: -8 } }
      ]
    },
    {
      label: "Weld the airlock shut",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.9, text: "A bead of molten steel, and the knocking stops. It does not stop quickly. It stops slowly, as if whoever is out there is thinking about it. The airlock is no longer an airlock. It is a wall that you would rather not lean on.",
          damage: { morale: -4 } },
        { chance: 0.1, text: "The weld takes. The knocking resumes from behind it, muffled now, patient, in the same rhythm. You can hear it in the pipes for a week.",
          damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "dread-002",
  title: "Breathing in the Vents",
  category: "horror",
  weight: 0.6,
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "Something is in the ducts. Slow, wet breathing, in time with the air handlers and a half-second behind. When the crew goes silent, it does too. {crew} swears it said their name from the vent above their bunk, once, very kindly, as if asking them to come and see.",
  choices: [
    {
      label: "Seal the vents in that section",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.8, text: "Plates, bolts, a lot of tape. The breathing stops. The air in that wing gets stale and thick, and nobody wants to tell you why they have started sleeping in the galley.",
          damage: { lifeSupport: -10, morale: -4 } },
        { chance: 0.2, text: "The breathing stops, and then restarts on the other side of the plate, closer to the floor, with a patient little sigh.",
          damage: { lifeSupport: -10, morale: -10 } }
      ]
    },
    {
      label: "Send {crew} in with a torch",
      detail: "Somebody has to look.",
      outcomes: [
        { chance: 0.55, text: "Forty minutes, a lot of crawling, an unreadable radio. {crew} comes out gray and quiet, holding a tangle of cable and one old coat, wet through. 'It's nothing,' they say. 'It was only that.' They do not sleep for two days.",
          effects: { parts: 1 }, damage: { morale: -8 } },
        { chance: 0.3, text: "The radio goes dead at minute nine. At minute ten it says {crew}'s voice, calmly, in a patient, reasonable tone: 'It's all right. Come in.' The crawlspace is empty. The hatch is on the inside, and it is shut.",
          damage: { morale: -14 }, crewDeath: true },
        { chance: 0.15, text: "{crew} finds a nest of warm cable, and a hum, and then a light. The air handlers all stop at once. When they start again, the breathing has been replaced by something very like a purr.",
          damage: { lifeSupport: -8, morale: -6 } }
      ]
    },
    {
      label: "Vent that section to vacuum",
      detail: "Whatever it is, it won't like the cold.",
      outcomes: [
        { chance: 0.75, text: "A rushing hiss, a bang, a long silence. The breathing stops. When the section is re-pressurized, there is nothing in the vents, and the vents are, somehow, slightly warm.",
          effects: { oxygen: -15 }, damage: { morale: -3 } },
        { chance: 0.25, text: "The air goes. The breathing does not. In the silence of vacuum, you can still hear it, thinly, through the hull itself, with a faint, amused rasp.",
          effects: { oxygen: -15 }, damage: { morale: -12, sensors: -5 } }
      ]
    }
  ]
},
{
  id: "dread-003",
  title: "The Sleepwalker",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "Three nights running, {crew} has gotten up, walked to the airlock, and stood facing it with their eyes open and empty, reciting numbers in a flat, careful voice. In the morning, {crew} remembers nothing, except an ache in the jaw, as though they had held a word in their mouth all night.",
  choices: [
    {
      label: "Lock {crew}'s cabin at night",
      outcomes: [
        { chance: 0.6, text: "The door holds. In the morning {crew} is on the floor beside it, in the dark, one hand pressed flat on the metal, face wet, and has no idea why. The numbers are scratched, very faintly, in the paint of the door.",
          damage: { morale: -8 } },
        { chance: 0.4, text: "The door holds. The other door holds. At 0300 the airlock sounds its alarm anyway. The outer door has been opened, and closed, and on the deck is a wet circle the size of a person, and a very faint smell of seawater.",
          effects: { oxygen: -8 }, damage: { morale: -10, hull: -5 } }
      ]
    },
    {
      label: "Follow {crew} and write down the numbers",
      detail: "Somebody wants to say something.",
      outcomes: [
        { chance: 0.55, text: "Eleven digits, repeated four times. Your navigator checks them in the morning, and her face goes odd. They are coordinates, and they are accurate. {crew} will not go near the airlock again.",
          damage: { morale: -10 }, surveyBonus: 0.1 },
        { chance: 0.45, text: "You follow. You write. The numbers are the ship's own coordinates, to the meter, and a few seconds ahead of the sensors. Then another set, and another, each a few seconds further on. You stop writing. You do not like where they are leading.",
          damage: { morale: -12, astrogation: -8 } }
      ]
    },
    {
      label: "Wake {crew} gently, then medicate",
      cost: { meds: -1 },
      outcomes: [
        { chance: 0.6, text: "A hand on the shoulder, a quiet word, and {crew} wakes, blinks, and says, very politely, 'Oh. Hello. Sorry. I was in the middle of something.' The numbers stop. Everything stops, for now. {crew} sleeps in the infirmary after that.",
          repair: { morale: 2 }, effects: { meds: -1 } },
        { chance: 0.4, text: "{crew} wakes, looks at you, and says in a voice not quite theirs: 'You woke me too early.' Then, in their own: 'Sorry. I don't know why I said that.' You decide to forget it. The infirmary lights flicker all night.",
          damage: { morale: -10 }, effects: { meds: -1 } }
      ]
    }
  ]
},
{
  id: "dread-004",
  title: "One Short",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  requires: { resourceAbove: { crew: 2 } },
  text: "The lights go out for eleven seconds. When they return, the bridge roster has one fewer name and nobody can say whose. The galley has one fewer chair. Your own log has a gap in it, exactly the shape of a person. Everyone swears the numbers are right. Nobody can look at {crew2}.",
  choices: [
    {
      label: "Check the cameras",
      outcomes: [
        { chance: 0.7, text: "Eleven seconds of perfect dark on every feed. Then {crew} on camera, looking up at the ceiling, walking out of frame the way people do when someone calls their name. {crew} is found in a locker, asleep, certain they were up the whole time.",
          damage: { morale: -6 }, delayDays: 1 },
        { chance: 0.3, text: "Eleven seconds of dark. Then {crew} looking up at the ceiling, and walking out of frame. No door opens. No door is open. The feed ends. The roster does not come back.",
          damage: { morale: -12 }, crewDeath: true }
      ]
    },
    {
      label: "Hold a roll call, out loud",
      outcomes: [
        { chance: 0.5, text: "Everyone is present. The count is right. The chairs are wrong. Nobody sits down for the rest of the day, and the number of chairs is argued about until dawn.",
          damage: { morale: -8 }, delayDays: 1 },
        { chance: 0.3, text: "Everyone answers. Then a name you do not know answers. The voice is cheerful. The person is there, cheerful, in the doorway, and everyone aboard knows them. You do too. You know them like a song.",
          effects: { crew: 1 }, damage: { morale: -8 }, delayDays: 1 },
        { chance: 0.2, text: "Everyone answers except {crew}, who is standing in front of you with a mouth shaped like the answer and no sound coming out. They keep trying. The mouth moves. A good minute of it.",
          damage: { morale: -14 }, crewDeath: true, delayDays: 1 }
      ]
    },
    {
      label: "Pretend it didn't happen",
      detail: "Lights flicker. That's all.",
      outcomes: [
        { chance: 0.9, text: "You say it was a blip. Nobody argues. Every crew member quietly checks the others' faces for the next three days.",
          damage: { morale: -10 } },
        { chance: 0.1, text: "You say it was a blip. In the morning, the blip has a name, written on the galley wall in a hand nobody recognizes: {crew}'s. And a time. Which is today.",
          damage: { morale: -14 }, crewDeath: true }
      ]
    }
  ]
},
{
  id: "dread-005",
  title: "A Voice We Buried",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  requires: { minDead: 1 },
  text: "The comms crackle on an empty band. It is {dead}'s voice, warm, familiar, a little hoarse. 'It's so cold out here,' it says. 'Open the outer hatch? Just for a minute. I only want to come in.' It says your name. It says it exactly the way they used to.",
  choices: [
    {
      label: "Refuse, and cut the channel",
      outcomes: [
        { chance: 0.85, text: "The voice does not argue. It says, 'All right,' very softly, and the carrier hiss resumes. You stand by the console for an hour afterwards. Nobody asks what you are listening for.",
          damage: { morale: -8 } },
        { chance: 0.15, text: "The voice says 'All right,' and the cut channel goes on saying it, from every speaker on the ship, in a calm, low, rising chorus that stops at exactly dawn.",
          damage: { morale: -12, sensors: -5 } }
      ]
    },
    {
      label: "Talk to it",
      detail: "Ask what it wants. Ask what it knows.",
      outcomes: [
        { chance: 0.45, text: "It talks for an hour. Mostly kindness. Mostly memories. Near the end it tells you where, in the dark ahead, something useful is drifting. You find it. You do not look at the crew's faces for a while.",
          effects: { food: 10, parts: 2 }, damage: { morale: -10 } },
        { chance: 0.55, text: "It talks. It knows what you said at the grave. It knows what you thought. It knows all of it, with a sad fondness, as if forgiving you in advance. The crew listens to every word. Some of them cry. Everyone is quieter for days.",
          damage: { morale: -16 } }
      ]
    },
    {
      label: "Record the signal and analyze it",
      cost: { power: -3 },
      outcomes: [
        { chance: 0.6, text: "A clever bit of work: the voice is assembled from pieces of the crew's own recordings, cut together with a very patient hand. Knowing that helps, a little. It does not stop anyone from listening to it again.",
          damage: { morale: -5 }, repair: { sensors: 5 } },
        { chance: 0.4, text: "Analysis finds nothing, no source, no origin, just a voice shaped like a person, sitting inside a carrier wave that did not come from outside. It came from the ship. It came from you.",
          damage: { morale: -10, sensors: -5 } }
      ]
    }
  ]
},
{
  id: "dread-006",
  title: "The Hull Is Warm",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  sensorWarning: "Thermal anomaly on the aft hull. Rhythmic. Sixty-one beats per minute.",
  text: "Panel 14C is warm. Then it is warmer. Then it moves, slowly, in and out, in time with something you can feel in the soles of your feet. Sixty-one beats a minute. The paint has gone soft there. {crew} laid a hand on it and said, in a small voice, 'It knows me.'",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Cut the panel out, EVA",
      cost: { parts: -3 },
      requires: { resources: { parts: 3 } },
      outcomes: [
        { chance: 0.6, text: "A long, careful cut. Underneath, a blister the size of a bed, pulsing. It goes slack when you slice through, and flattens with a sigh. The hull behind it is clean, scarred, and a little pink. You patch it, fast.",
          repair: { hull: 10 }, damage: { morale: -8 }, delayDays: 1 },
        { chance: 0.25, text: "The cut bleeds something warm and clear. It coats the suit of the person holding the torch. {crew} is fine. {crew} is better than fine. {crew} says they feel wonderful, and will not stop smiling.",
          damage: { morale: -10, hull: -5 }, delayDays: 1 },
        { chance: 0.15, text: "The blister opens before the cut is done. It is not a blister. It is a mouth, small, and very patient, and it takes the torch, and the hand holding it.",
          damage: { morale: -14, hull: -10 }, crewDeath: true }
      ]
    },
    {
      label: "Weld over it",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.8, text: "Steel over flesh. The pulse is muffled, and then, with an effort, silenced. For days the weld is warm. The crew walks around that section of the ship in a wide, silent arc.",
          damage: { morale: -6 } },
        { chance: 0.2, text: "The weld cracks in the night. By morning it has healed over, smooth, the color of an old scar. The pulse has gone, but the panel is thicker than it was.",
          damage: { hull: -10, morale: -8 } }
      ]
    },
    {
      label: "Leave it, and watch it",
      detail: "Maybe it's only the sun.",
      outcomes: [
        { chance: 0.5, text: "It beats for a week, and fades, and does not return. The hull, in the end, is stronger there than before. Nobody knows why. Everybody knows why.",
          repair: { hull: 8 }, damage: { morale: -6 } },
        { chance: 0.5, text: "It beats for a week. It beats faster. In the last hour it matches the pulse of the nearest sleeper, then the next, then all of them. In the morning there is a very small tear in the seam and a smell of hospitals.",
          damage: { hull: -15, morale: -8 }, effects: { oxygen: -6 } }
      ]
    }
  ]
},
{
  id: "dread-007",
  title: "Reflections",
  category: "horror",
  weight: 0.6,
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "The mirror in the washroom is a half-second behind. Every window, every polished panel, every dark screen. It started with {crew}, who noticed their reflection finishing a gesture after they had stopped. Now it is everywhere. Your own, you notice, is looking at something over your shoulder.",
  choices: [
    {
      label: "Cover every reflective surface",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.8, text: "Tarps, tape, a dull afternoon. The ship goes dim, and a little blind. Nobody brushes their teeth by sight. Nobody looks. After three days the tarps are rolled up in a corner. Nobody asks.",
          damage: { morale: -4 } },
        { chance: 0.2, text: "Under the second tarp, something in the glass is moving. The tarp bulges outward, once, like a held breath, and then lies flat again. The sensors on that deck go blind for a day.",
          damage: { morale: -8, sensors: -8 } }
      ]
    },
    {
      label: "Show {crew} their reflection",
      detail: "It might help to see it plainly.",
      outcomes: [
        { chance: 0.5, text: "{crew} looks. The reflection looks back, steady, and smiles an instant late. {crew} smiles, a moment after that. It is, at least, a smile. The two of them part on good terms, whatever that means.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "{crew} looks and smashes the mirror with a fist. Glass. A great deal of blood. {crew} is quiet and sorry, and for the rest of the day, does not look anyone in the eye.",
          effects: { meds: -1 }, damage: { morale: -8 } },
        { chance: 0.2, text: "{crew} looks. The reflection does not. It steps forward, and the glass is a door. Nobody sees what happens next. The washroom is empty. In the mirror, someone is brushing their teeth, perfectly calm.",
          damage: { morale: -14 }, crewDeath: true }
      ]
    },
    {
      label: "Look at your own, as long as it takes",
      detail: "Somebody has to.",
      outcomes: [
        { chance: 0.5, text: "An hour. Your reflection holds your gaze at last, and nods, once, with some difficulty. You nod back. It is the strangest conversation you have ever had, and the shortest.",
          repair: { morale: 4 }, delayDays: 1 },
        { chance: 0.5, text: "An hour. Then it turns to look behind you, with an expression that you feel in your spine. You do not turn around. You do not turn around for the rest of the day, and you are never quite alone.",
          damage: { morale: -10 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "dread-008",
  title: "The Smiling Ship",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  sensorWarning: "Derelict, intact, powered. Thirty-one life signs, none moving.",
  requires: { resourceAbove: { crew: 1 } },
  text: "A freighter, intact, lights on, drifting in a slow, perfect circle. Through the viewports, a crew of thirty stands at their stations, in clean uniforms, hands at their sides. They are smiling. They are all facing aft. Nobody has moved in the whole time you have watched, and that is a long time.",
  choices: [
    {
      label: "Board it",
      detail: "Thirty people, and everything they own.",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.5, text: "Clean decks, full lockers, calm air. Nothing moves. Nothing wants to. The boarding party fills their packs and leaves quickly, without turning their backs. Not one of them looks at the faces.",
          effects: { parts: 4, food: 10 }, damage: { morale: -10 } },
        { chance: 0.3, text: "The boarding party returns with their packs full and a faint, pleasant smile each, as if at a good thought. For two days they talk about how peaceful it was. They sleep facing aft.",
          effects: { parts: 3, meds: -3 }, damage: { morale: -12 }, delayDays: 2 },
        { chance: 0.2, text: "Someone does not return. The airlock cycles a minute after the others are out of it. {crew} is standing in it, smiling, facing aft. They have always been here. They are fine. They are so much better now.",
          damage: { morale: -16 }, crewDeath: true }
      ]
    },
    {
      label: "Send a retrieval drone for their logs",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.7, text: "The drone brings back a data slate. The last entry, in a steady hand: 'It's so nice to finally be facing the right way.' The slate also has their course, clean and perfect, which is more than your own navigator has had all week.",
          repair: { astrogation: 10 }, damage: { morale: -8 } },
        { chance: 0.3, text: "The drone returns dented, empty, and smiling, in the way that dented machines cannot, but this one does. The crew stares at it. Someone covers it with a blanket.",
          damage: { morale: -8, sensors: -5 } }
      ]
    },
    {
      label: "Leave. Now.",
      outcomes: [
        { chance: 1, text: "You turn the ship, and as you do, thirty heads turn with it, in perfect unison, to keep facing you. They do not stop smiling. You do not look back. Nobody speaks for a long time.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "dread-009",
  title: "The Same Dream",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  text: "Everyone aboard has had the same dream: a hallway of tall, quiet doors, and a voice saying, 'Not that one. Not that one. That one.' Every crew member wakes at the same moment and says the same word, which none of them can remember. Everyone dreams of that word. Everyone wants to dream of it again.",
  choices: [
    {
      label: "Medicate everyone for dreamless sleep",
      cost: { meds: -3 },
      requires: { resources: { meds: 3 } },
      outcomes: [
        { chance: 0.8, text: "Black, empty, welcome sleep. The crew wakes rested and strangely hollow. The word is gone. A little of everyone has gone with it.",
          repair: { morale: 4 } },
        { chance: 0.2, text: "The medicine works for everyone but you. You walk the hall alone, door after door after door, and when you wake you are standing at the airlock, and the voice is saying 'That one.'",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Go through the door together, tonight",
      detail: "It's the same dream. Somebody should answer.",
      outcomes: [
        { chance: 0.45, text: "You go together, hand in hand, down a hall without end, through a door that opens like a held breath. Beyond is a great, calm, moving dark, and the sense of being known. You wake with a clear idea of where the stars are. It does not feel earned.",
          repair: { astrogation: 12, morale: 4 } },
        { chance: 0.55, text: "You go. The door opens. A whole day vanishes from the log, and from everyone's heads. When you wake, everyone is thin, and hungry, and weeping, and no one will say why.",
          effects: { food: -10, water: -6 }, damage: { morale: -12 }, delayDays: 1 }
      ]
    },
    {
      label: "Keep everyone awake",
      detail: "It can't get you if you don't sleep.",
      outcomes: [
        { chance: 0.7, text: "Coffee, cards, and long, restless talk. By day two everyone is a bit slap-happy and a bit mean. By day three the dream has gone looking for someone easier.",
          effects: { food: -6 }, damage: { morale: -10 }, delayDays: 1 },
        { chance: 0.3, text: "Day two. Day three. Day four. The crew begins, one by one, to dream with their eyes open, and to say the word, softly, into the corners of the ship.",
          damage: { morale: -14, sensors: -8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "dread-010",
  title: "Something Is Not Reflecting",
  category: "horror",
  weight: 0.6,
  zones: ["deep"],
  sensorWarning: "A region of space is absorbing all returns. It is roughly the size of a moon. It is moving with you.",
  text: "A gap in the stars, shaped like nothing a star was ever shaped like, keeps pace at the edge of sensor range. It does not reflect. It does not radiate. The crew can feel it on the backs of their necks, the way you can feel a stranger's gaze. It has not yet decided what you are.",
  choices: [
    {
      label: "Lights out, engines idle, silence",
      detail: "Be a rock. Be a very small rock.",
      outcomes: [
        { chance: 0.7, text: "Two days of dark and held breath. The gap pauses over the ship for a long, long moment, like a hand over a candle. Then it moves on, with what might be an indifferent shrug. The crew does not look up for a long time.",
          damage: { morale: -8 }, delayDays: 2 },
        { chance: 0.3, text: "It touches the ship. Once. Very lightly. Every plate on the dark side sings, and the cold that comes through is not temperature. It leaves. The hull behind where it touched is bright, and brittle, and tired.",
          damage: { morale: -10, hull: -20 }, delayDays: 2 }
      ]
    },
    {
      label: "Run",
      detail: "It has not decided yet. Make it hard.",
      cost: { fuel: -15 },
      outcomes: [
        { chance: 0.5, text: "You push everything you have. The gap falls behind, and behind, with the quiet, disappointed air of something that never really wanted to chase. By the time you slow, you are very tired and very alone.",
          damage: { morale: -4, engines: -5 } },
        { chance: 0.5, text: "You run. It keeps pace without moving. The engines scream, the crew screams, the fuel gauge empties, and when at last you stop it is exactly where it was, patiently, a short way off.",
          damage: { engines: -15, morale: -8 } }
      ]
    },
    {
      label: "Hail it",
      detail: "It's listening. Say something.",
      cost: { power: -6 },
      outcomes: [
        { chance: 0.4, text: "You say what comes. 'We're only passing through. We mean no harm.' A long time passes. The gap turns, slowly, a degree at a time, and goes elsewhere, as if bored by politeness.",
          repair: { morale: 4 } },
        { chance: 0.3, text: "You hail it. It takes interest. It takes {crew}. Gently, as one takes a fruit off a branch. {crew} stands at the viewport for an hour with no reflection, and then is simply not there. The gap, satisfied, withdraws.",
          damage: { morale: -16 }, crewDeath: true },
        { chance: 0.3, text: "It does not answer, but the stars come back, one at a time, all around you, as if relieved. The gap is gone. Your comms array is full of a long, low tone, and the log has a day missing.",
          damage: { morale: -6, sensors: -10 }, delayDays: 1 }
      ]
    }
  ],
  requires: { resourceAbove: { crew: 1 } }
},
{
  id: "dread-011",
  title: "One Airlock Too Many",
  category: "horror",
  weight: 0.6,
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "The ship has an airlock that is not on any schematic. A panel in the aft corridor came loose in a burn, and behind it is a small, clean hatch, with a handle worn smooth. Inside the window: a wet footprint, the size of a child's. It is not yours. It is not the crew's. It is still wet.",
  choices: [
    {
      label: "Open it",
      outcomes: [
        { chance: 0.4, text: "A narrow, spotless, dry cupboard. Racked inside: fuel cells, still sealed, and a toolkit of great age, wrapped in clean cloth. A note in a hand you do not know: 'For when it's worse.' You take it all. It is worse.",
          effects: { fuel: 14, parts: 3 }, damage: { morale: -4 } },
        { chance: 0.4, text: "Empty. Warm. A faint smell of a house in summer. The footprint is gone. A great many small, dry prints, in the dust of the walls, as if someone had walked the room many times, round and round, and then gone out the way they came. Which was not the door.",
          damage: { morale: -10 } },
        { chance: 0.2, text: "Empty. Warm. When you shut the hatch, something taps on it, politely, from inside. By nightfall, there are sounds in the corridors, small, soft, and moving, and {crew} has stopped sleeping in their own bunk.",
          damage: { morale: -14, sensors: -5 } }
      ]
    },
    {
      label: "Weld it shut",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.9, text: "Steel over steel. It is a small, quiet, careful job, and when it is done, nobody talks about the hatch. The panel is put back. The footprint, which has been drying all this time, is almost gone.",
          damage: { morale: -4 } },
        { chance: 0.1, text: "The weld takes. In the morning, the panel is off, the weld is neatly cut away from the inside, and the footprint is wet again. A second, smaller one has joined it.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Put the panel back and say nothing",
      outcomes: [
        { chance: 0.8, text: "It is only a hatch. It is only a footprint. You go to bed, and for a long time you do not sleep, listening to the corridor, which is quiet, and listening very hard.",
          damage: { morale: -6 } },
        { chance: 0.2, text: "It is only a hatch. For two nights it is quiet. On the third, someone, somewhere in the ship, begins to open and close it, very softly, over and over, like a person who cannot decide.",
          damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "dread-012",
  title: "A Note in Your Own Hand",
  category: "horror",
  weight: 0.6,
  zones: ["outer", "deep"],
  text: "On your pillow, a folded note in your own handwriting. Dated tomorrow. It reads, in full: 'DON'T TRUST THE NEXT PERSON WHO SAYS IT'S FINE.' The ink is not dry. It does not smell of ink. It smells, faintly, of a hospital, and of someone who has been crying.",
  choices: [
    {
      label: "Tell the crew",
      outcomes: [
        { chance: 0.6, text: "You read it aloud. A cold silence. For days, every reassurance on the ship is received with narrowed eyes and a quiet, 'Is it, though?' Nobody says 'fine' for a week. Efficiency, strangely, improves.",
          damage: { morale: -8 }, repair: { sensors: 6 }, flag: "warned_by_self" },
        { chance: 0.4, text: "You read it aloud. Everyone nods. A tall quiet one, at the back, says, softly, 'Well, that's all right then,' and the room goes very still. Nobody knows who said it. Everybody remembers the voice.",
          damage: { morale: -12 }, flag: "warned_by_self" }
      ]
    },
    {
      label: "Tell no one",
      outcomes: [
        { chance: 0.7, text: "You fold the note into a pocket. You carry it for days. You say 'fine' as little as you can. Nobody notices, and it is a lonely, careful, watchful way to be.",
          damage: { morale: -6 }, repair: { sensors: 4 }, flag: "warned_by_self" },
        { chance: 0.3, text: "You fold the note into a pocket. In the morning, there is a second one on the pillow, and the first is blank, and the second is in a different hand. It is yours, still. It says: 'Too late.'",
          damage: { morale: -10 }, flag: "warned_by_self" }
      ]
    },
    {
      label: "Burn it",
      outcomes: [
        { chance: 0.5, text: "It burns very fast and does not curl. The ash holds the shape of the words for a full second. You open the vent and send it all out, and the crew is none the wiser. You would like to be.",
          damage: { morale: -4 } },
        { chance: 0.5, text: "The note burns. The smell fills the cabin, hospital and tears, and doesn't leave for days. A new note is in the ash, perfectly clean: 'Thank you for being careful.' It is the worst thing yet.",
          damage: { morale: -10 }, flag: "warned_by_self" }
      ]
    }
  ]
}
);
