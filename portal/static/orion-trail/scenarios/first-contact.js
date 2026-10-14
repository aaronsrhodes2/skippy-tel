/* ORION TRAIL — first contact. Prefix: alien- . Outer system and deep space.
   Weight ~0.7: strange, not constant. The universe, it turns out, has bureaucracy. */
SCENARIOS.push(
{
  id: "alien-001",
  title: "The Primes",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A signal on every band: two, three, five, seven, eleven. Over and over, patient as a metronome. Someone out there wants you to know they can count. The crew crowds the console. Somebody whispers, 'Do we answer?' It is not a joke.",
  choices: [
    {
      label: "Answer with the next prime",
      detail: "Politely. Slowly. With power you don't have.",
      cost: { power: -6 },
      outcomes: [
        { chance: 0.7, text: "The signal pauses. Then: thirteen. The crew cheers like a stadium. Whatever it is, it is willing to keep counting with you. You file a note for later.",
          repair: { morale: 10 }, flag: "sent_greeting" },
        { chance: 0.3, text: "The signal stops. It does not resume. A long, thick silence, and you have no way to know if you said the right thing or the rudest one.",
          damage: { morale: -6 }, flag: "sent_greeting" }
      ]
    },
    {
      label: "Say nothing and log it",
      outcomes: [
        { chance: 0.75, text: "You record it, and the signal fades with the distance. The crew argues for two days about what you did. The argument is mild. The argument is thorough.",
          damage: { morale: -4 } },
        { chance: 0.25, text: "The signal doesn't fade. It follows you through three jumps, counting patiently, and then gets bored, and in doing so, takes your sensors with it.",
          damage: { morale: -6, sensors: -10 } }
      ]
    },
    {
      label: "Jam it",
      detail: "Whatever it is, it isn't yours.",
      cost: { power: -4 },
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.85, text: "Static replaces mathematics. The crew looks at you. It is the look people give when they've seen you do something a little too decisive.",
          damage: { morale: -8 } },
        { chance: 0.15, text: "The jam sprays back along your own antenna. The comms array fries itself in protest.",
          damage: { morale: -8, sensors: -10 }, effects: { power: -6 } }
      ]
    }
  ]
},
{
  id: "alien-002",
  title: "The Courteous Swarm",
  category: "first-contact",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A cloud of tiny machines drifts out of the dark and settles on the hull like a polite fog. Text scrolls across your console, grammatically perfect and faintly apologetic: 'We require iron. We are so sorry to ask. We can pay in labor.'",
  choices: [
    {
      label: "Give them spare parts",
      detail: "They said please.",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.8, text: "They eat the parts down to dust, then spend an afternoon sanding your hull and re-bonding seams. When they leave, they leave a tiny bow. You did not know machines could bow.",
          repair: { hull: 20 } },
        { chance: 0.2, text: "They overdo it. The hull gleams, but the swarm takes a few things they did not ask for. The sensors are quietly missing a lens.",
          repair: { hull: 12 }, damage: { sensors: -10 } }
      ]
    },
    {
      label: "Refuse politely",
      outcomes: [
        { chance: 0.8, text: "'We understand,' the text says. 'Sorry for the trouble.' The cloud leaves, sad and perfectly well-mannered. The crew feels like it has kicked a dog.",
          damage: { morale: -6 } },
        { chance: 0.2, text: "'We understand,' the text says, and takes a tiny bit of iron from the hull anyway, as a surcharge on politeness.",
          damage: { hull: -10, morale: -4 } }
      ]
    },
    {
      label: "Haggle: labor for a lot more than iron",
      detail: "They did offer.",
      outcomes: [
        { chance: 0.5, text: "You come away with a full systems tune-up and an unexplained jar of fuel. They really are very sorry about the whole thing.",
          repair: { engines: 10, generator: 10 }, effects: { fuel: 6, parts: -1 } },
        { chance: 0.5, text: "They agree too quickly and leave a minor mess in the guts of your engines. A very sincere note says, 'This should have worked.'",
          damage: { engines: -12 }, effects: { fuel: 6 } }
      ]
    }
  ]
},
{
  id: "alien-003",
  title: "The Mirror Ship",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A ship matches your course. It has your hull, your paint, your name in reverse: {ship}. Its running lights blink in your pattern. On the viewport, you can see a shape at the helm waving back, one second behind you. {crew} says it is a reflection. It is not a reflection.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Hail it",
      outcomes: [
        { chance: 0.5, text: "It hails you back with the exact same words, a second ahead. Then, after a long moment, it offers a supply pod. It's the most polite thing that has ever copied you.",
          effects: { food: 10, water: 8 }, damage: { morale: -4 } },
        { chance: 0.5, text: "It answers before you finish. It answers, in fact, before you begin. A line of your own log appears in your console in someone else's handwriting. {crew} leaves the bridge and does not return for hours.",
          damage: { morale: -10, sensors: -5 } }
      ]
    },
    {
      label: "Burn away",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.7, text: "It lets you go. Or perhaps you let it go. It is hard to say. At the edge of sensor range it blinks once, as if saying goodbye, and the blink lingers on your screen for a day.",
          damage: { morale: -3 } },
        { chance: 0.3, text: "You burn. It burns. Neither of you gets anywhere, and the engines pay for the symmetry.",
          damage: { engines: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Board it",
      detail: "Armed, suited, honest about the risks.",
      requires: { systemOk: ["weapons"], minCrew: 3 },
      outcomes: [
        { chance: 0.4, text: "Empty. Spotless. Fully stocked, with a version of your log in your own voice. The food in the galley tastes like your mother's, and you do not say so.",
          effects: { food: 18, parts: 2 }, damage: { morale: -6 } },
        { chance: 0.6, text: "Empty. Identical. Wrong in a way nobody can name. You take what you can carry, and no one speaks for a day. {crew} refuses to go back for the rest.",
          effects: { parts: 2, meds: 1 }, damage: { morale: -10 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "alien-004",
  title: "The Quiet Buoy",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A buoy in your path pulses a pattern the translator renders as a single word: SHHH. A boundary of some kind sits ahead, and the buoy asks, with real feeling, for silence. No transmissions. No radar. No engines louder than a whisper. For about three days.",
  choices: [
    {
      label: "Comply",
      detail: "Coast. Whisper. Don't breathe loudly.",
      outcomes: [
        { chance: 0.75, text: "Three days of low power and lower voices. The crew learns what a ship sounds like when nobody's talking: a heartbeat, a pipe, a very distant 'thank you' that you do not dwell on.",
          repair: { morale: 4 }, effects: { fuel: -4 }, delayDays: 3 },
        { chance: 0.25, text: "Day two: somebody sneezes. The buoy does not take it well. The silence turns oily, and the sensors glitch out for a while in sympathy.",
          damage: { morale: -4, sensors: -10 }, effects: { fuel: -4 }, delayDays: 3 }
      ]
    },
    {
      label: "Ignore it and burn on",
      detail: "A buoy is a buoy.",
      outcomes: [
        { chance: 0.5, text: "Nothing happens. You pass through the boundary with all the noise your engines can muster, and the buoy goes quiet in a way you suspect is disappointed.",
          damage: { morale: -3 } },
        { chance: 0.5, text: "The boundary is not a metaphor. Something enormous, and patient, and extremely annoyed, leans against the hull for a second. When it lets go, your plating has a very large handprint.",
          damage: { hull: -20, morale: -8 } }
      ]
    },
    {
      label: "Try to negotiate",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.55, text: "You broadcast a very short, very soft message. The buoy considers, then pulses a different word: ALLOWED. It lets you through in two days, not three, and trails a faint blue light behind you like a blessing.",
          delayDays: 2, repair: { morale: 4 } },
        { chance: 0.45, text: "You broadcast something soft that it hears as loud. The buoy sighs. A long, resentful sigh, in hard radiation.",
          damage: { morale: -6, generator: -8 }, delayDays: 3 }
      ]
    }
  ]
},
{
  id: "alien-005",
  title: "The Gardener",
  category: "first-contact",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "A drifting garden hangs in the dark, a lattice of glass and green, tended by something with too many arms. It sees you. It holds up a fruit the color of a sunrise you've never seen. It does not speak. It does not need to.",
  choices: [
    {
      label: "Trade water for fruit",
      cost: { water: -10 },
      outcomes: [
        { chance: 0.8, text: "A handful of strange fruit for a handful of water, a deal as old as hunger. They taste of citrus and smoke and a childhood you didn't have. The crew sleeps well.",
          effects: { food: 22 }, repair: { morale: 6 } },
        { chance: 0.2, text: "The fruit is delicious. The fruit is also, in some quantity, mildly hallucinogenic. The ship has a very odd, very productive night.",
          effects: { food: 22 }, damage: { morale: -4, astrogation: -10 } }
      ]
    },
    {
      label: "Take it by force",
      detail: "It's a lot of food. It's one gardener.",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.5, text: "The gardener doesn't resist. It watches you pick and haul with a look of enormous patience, and then it grows a fruit directly in your fuel line, as a fine.",
          effects: { food: 30 }, damage: { morale: -10, engines: -8 } },
        { chance: 0.5, text: "The garden defends itself with a thousand tiny, precise vines. You lose a lens, a cable, and your self-image.",
          effects: { food: 14 }, damage: { hull: -15, morale: -8 } }
      ]
    },
    {
      label: "Wave back and go",
      outcomes: [
        { chance: 1, text: "The gardener waves back, all arms at once. A small cluster of seeds drifts toward your airlock and sticks to the hull. You'll find them in the vents, later, blooming.",
          repair: { morale: 5 }, effects: { fuel: -3 } }
      ]
    }
  ]
},
{
  id: "alien-006",
  title: "Distress, Not Ours",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A beacon in a language no human has spoken. The translator offers a best guess: 'FAMILY. COLD. PLEASE.' Three small shapes cling to a drifting hull. Whatever they are, they are shivering. Your sensors say they are about the size of dogs and have too many eyes.",
  choices: [
    {
      label: "Take them aboard",
      detail: "They said please.",
      cost: { food: -6, water: -6 },
      outcomes: [
        { chance: 0.55, text: "They tuck into the corners of the ship like cats. In thanks, they leave their hull lamp, a flask of rare coolant, and a smell of cinnamon that lingers for weeks.",
          effects: { parts: 2, fuel: 8 }, repair: { morale: 10 } },
        { chance: 0.45, text: "They are sick. The sickness is not contagious. It is, however, the sort of sick that requires a doctor, a day, and a great many things from the med locker.",
          effects: { meds: -3 }, damage: { morale: -4 }, delayDays: 1 }
      ]
    },
    {
      label: "Send coordinates to the nearest port and move on",
      detail: "Nobody there. Technically, though, you sent it.",
      outcomes: [
        { chance: 1, text: "A message goes out into the dark, to a port that has been dead for sixty years. It takes the crew a while to stop looking at the screen where the shapes shrink behind you.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Give them heat and supplies, then go",
      cost: { power: -8, food: -4 },
      outcomes: [
        { chance: 0.85, text: "You patch the hull with a bladder of warm air and a roll of tape, and they flash their lamps in a long thank-you pattern. It is a better exit than most people get.",
          repair: { morale: 6 } },
        { chance: 0.15, text: "The patch holds. The power link holds less. The ship's generator takes a hit when the heat bleeds into your own grid.",
          repair: { morale: 4 }, damage: { generator: -10 } }
      ]
    }
  ]
},
{
  id: "alien-007",
  title: "The Archivist",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A derelict intelligence in a pearl-colored shell rouses as you pass. In flawless, tired English, it says it will answer a single question before it falls asleep again forever. It has been waiting a very long time for someone to ask anything at all.",
  choices: [
    {
      label: "Ask: which way is home?",
      outcomes: [
        { chance: 0.8, text: "It gives you a vector, a number, and a small tired sigh. Your navigator weeps. 'Fine,' she says. 'Fine. Okay.' The star map has never looked more honest.",
          repair: { astrogation: 15 }, damage: { morale: -4 } },
        { chance: 0.2, text: "'Home is not a direction,' it says, and sleeps. You are out a day of staring at nothing, and the crew has opinions.",
          damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Ask: how do I fix my ship?",
      outcomes: [
        { chance: 0.7, text: "A flood of diagrams, notes, and one personal remark about your wiring. Every system on the ship gets a small, clever improvement. The archivist sleeps with something like satisfaction.",
          repair: { engines: 10, generator: 10, lifeSupport: 10 }, effects: { power: -6 } },
        { chance: 0.3, text: "It answers in the language of its builders, which is mostly pressure. Your sensors give up trying to translate and take a nap.",
          damage: { sensors: -15 } }
      ]
    },
    {
      label: "Ask nothing. Thank it.",
      detail: "Even old machines might like being thanked.",
      outcomes: [
        { chance: 0.65, text: "It does not answer. It does something better: it pulses a warm, long light through the viewports, and the crew stands in it like sunlight. The weeks ahead feel a little shorter.",
          repair: { morale: 12 } },
        { chance: 0.35, text: "It takes this as an insult. It deletes your star charts on its way down, with the elegance of a very old man leaving a bad party.",
          damage: { astrogation: -15, morale: -4 } }
      ]
    }
  ]
},
{
  id: "alien-008",
  title: "Customs",
  category: "first-contact",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A very small, very official ship pulls alongside. Its pilot, a thing like a heron in a necktie, requires your manifest. Whatever jurisdiction this is, it has been enforcing it since before your species could hold a pencil. It would like to know if you have anything to declare.",
  choices: [
    {
      label: "Declare everything honestly",
      detail: "Paperwork is the oldest magic.",
      outcomes: [
        { chance: 0.85, text: "A stamp comes down. A tax is levied, small and sensible, and a receipt prints in four alphabets. The heron wishes you a pleasant journey in what you suspect is deeply sincere contempt.",
          effects: { food: -8, fuel: -6 }, repair: { morale: 2 } },
        { chance: 0.15, text: "You declared one thing wrong. It's tiny. It's also, in this jurisdiction, a felony of mild interest. The fine is steep. The apology is brisk.",
          effects: { food: -12, fuel: -10, parts: -1 } }
      ]
    },
    {
      label: "Hide a few things",
      detail: "What they don't see won't tax you.",
      outcomes: [
        { chance: 0.5, text: "The heron inspects, sniffs, stamps. You pass. You keep your supplies, and the crew spends the next day not looking at the one locker with the false bottom.",
          damage: { morale: -3 } },
        { chance: 0.5, text: "The heron finds the false bottom in under a minute. A very long form is produced. The fine is in food, fuel, and an apology you must read aloud to a camera.",
          effects: { food: -16, fuel: -12 }, damage: { morale: -8 } }
      ]
    },
    {
      label: "Bribe the inspector",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.7, text: "Two parts vanish into a sleeve the size of a small country. A new, longer, kinder stamp descends, and the heron looks up with a tiny, tired smile. 'Welcome,' it says, 'to the galaxy.'",
          repair: { morale: 3 } },
        { chance: 0.3, text: "The heron is deeply, thoroughly offended. It writes you up in four alphabets and takes the parts anyway, as evidence.",
          effects: { food: -8 }, damage: { morale: -6 } }
      ]
    }
  ]
}
);
