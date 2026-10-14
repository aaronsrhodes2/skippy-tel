/* ORION TRAIL — the bizarre and the utterly alien. Prefix: odd- .
   Not threats, exactly. Physics, time and logic going quietly wrong; things that are not hostile
   because they have not noticed you are a person. Mostly outer system and deep space. Weight ~0.7. */
SCENARIOS.push(
{
  id: "odd-001",
  title: "The Corridor Is Longer",
  category: "bizarre",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  requires: { resourceAbove: { crew: 1 } },
  text: "Corridor C is nineteen meters on the schematics, on the tape measure, and from outside. From inside, it has been getting longer. {crew} went for a wrench four hours ago and can be heard, faintly, still walking. The echo has an echo.",
  choices: [
    {
      label: "Tie a rope to the hatch and go in after {crew}",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.6, text: "Forty meters in, you find {crew} sitting calmly on the deck, holding a wrench. 'I got it right away,' they say. 'I just couldn't find the way back.' The corridor snaps to nineteen meters around you like a held breath released.",
          repair: { morale: 6 }, delayDays: 1 },
        { chance: 0.4, text: "The rope runs out. Then the rope ends, politely, in a clean cut. The corridor snaps back to nineteen meters. You are holding a rope with no far end. {crew} is not in the corridor.",
          damage: { morale: -12 }, crewDeath: true }
      ]
    },
    {
      label: "Seal the hatch and wait",
      outcomes: [
        { chance: 0.7, text: "Six hours later {crew} knocks from the galley side, rumpled and very sure it is tomorrow. It is not. They have the wrench. They will not say where they found it.",
          damage: { morale: -4 }, delayDays: 1 },
        { chance: 0.3, text: "The corridor returns in the night, shorter than before. Nineteen meters minus a locker, a bulkhead plate and a few cans of something. {crew} is back, wrench-less, and unwell.",
          effects: { parts: -3 }, damage: { morale: -6, hull: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "Call {crew} on the intercom",
      detail: "The cheapest tool you have. Also the one that might call back.",
      outcomes: [
        { chance: 0.5, text: "'Captain? I've been in the galley the whole time,' says {crew}. The wrench in the corridor is gone. A very long wrench, left lying along the whole deck, is not. It takes a crew of three to carry out.",
          effects: { parts: 2 }, damage: { morale: -5 } },
        { chance: 0.5, text: "The intercom answers in {crew}'s voice a moment before you speak. It says your question, kindly, and then a coordinate, and then it stops.",
          damage: { morale: -7, sensors: -8 } }
      ]
    }
  ]
},
{
  id: "odd-002",
  title: "Yesterday, Again",
  category: "bizarre",
  weight: 0.7,
  requires: { resourceAbove: { crew: 1 }, minDay: 12 },
  text: "Tuesday happens twice. Exactly: the same spilled coffee, the same alarm, {crew} telling the same unfunny joke in the same spot. The log has two entries for the day, and the second one is slightly more tired. Nobody mentions it. Everybody has noticed.",
  choices: [
    {
      label: "Do everything differently",
      detail: "Break the pattern. Whatever it costs.",
      outcomes: [
        { chance: 0.6, text: "You skip the coffee, take the long way to the bridge, ask {crew} a new question. The day clicks over like a stuck gear. Wednesday arrives with a faint, grateful chime.",
          repair: { morale: 5 }, delayDays: 1 },
        { chance: 0.4, text: "Everything different. Everything the same. The day resets again, with you a little less patient and the coffee a little colder. By the third Tuesday you are using the good water for something stupid.",
          effects: { food: -4, water: -6 }, damage: { morale: -6 }, delayDays: 2 }
      ]
    },
    {
      label: "Do everything exactly the same",
      detail: "Maybe it likes consistency.",
      outcomes: [
        { chance: 0.5, text: "You repeat your day to the second, down to the tone of the joke. At midnight the clock, satisfied, lets go. Nobody has ever been so careful about coffee.",
          delayDays: 1, damage: { morale: -3 } },
        { chance: 0.5, text: "You repeat the day, and the day, with the perfect obedience of a prisoner. Somewhere in the second pass, a valve you opened twice stays open.",
          effects: { oxygen: -8 }, damage: { engines: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Send yourself a note",
      detail: "Short. Honest. Unwise.",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.5, text: "It arrives in your own handwriting, which is slightly better than your handwriting. 'Change the second thing you do,' it says. You do. The day ends. You keep the note in a drawer and do not read it again.",
          repair: { morale: 4 }, delayDays: 1, flag: "warned_by_self" },
        { chance: 0.5, text: "The note arrives, and so does an answer, in the same hand, saying only: 'Stop.' The day ends because you stop. Neither of you mentions what you meant.",
          damage: { morale: -6, sensors: -5 }, delayDays: 1, flag: "warned_by_self" }
      ]
    }
  ]
},
{
  id: "odd-003",
  title: "The Color Nobody Can Name",
  category: "bizarre",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "The forward sensors paint a color on the screen that is not in the spectrum. {crew} says it is a little like cold, and a little like Thursday. The crew has gathered to look. Two of them are smiling. One has started to cry and cannot tell you why.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Filter it out of the sensor feed",
      cost: { power: -4 },
      outcomes: [
        { chance: 0.7, text: "The screen returns to ordinary colors. The crew blinks. Someone says 'Oh,' as if a nice song had just ended. The sensors, offended, are a little slower afterward.",
          damage: { sensors: -8, morale: -4 } },
        { chance: 0.3, text: "The color is not on the screen. It was never on the screen. It is, as it turns out, in the filter, and the filter now glows faintly in the dark.",
          damage: { sensors: -15, morale: -6 } }
      ]
    },
    {
      label: "Let the crew look",
      detail: "It seems peaceful. It seems very peaceful.",
      outcomes: [
        { chance: 0.55, text: "Six hours of silent watching, and everyone is lighter afterward, as if they had set something down. {crew} says they finally remember something good about their father.",
          repair: { morale: 12 }, delayDays: 1 },
        { chance: 0.45, text: "Six hours. Nobody blinked. When the color fades, there is a day of missing time, a drained tank of water, and a lingering sense that someone, once, asked something of you.",
          effects: { water: -10, food: -6 }, damage: { morale: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "Try to name it",
      detail: "Science demands a word.",
      outcomes: [
        { chance: 0.4, text: "{crew} says a word. It is a good word. The color considers it and goes away, evidently satisfied to have been called something. The word is now on the ship's pet fish, which was never a fish.",
          repair: { morale: 6, sensors: 6 } },
        { chance: 0.6, text: "You try seven names. The color rejects each one with a small ripple of disappointment. By the end the crew is arguing about names, and the argument has turned fierce.",
          damage: { morale: -8 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "odd-004",
  title: "The Manifest Writes Itself",
  category: "bizarre",
  weight: 0.7,
  requires: { resourceAbove: { crew: 1 } },
  text: "Overnight, the cargo manifest has grown new lines in a neat hand that isn't anyone's. '1x ocean (small).' '4x memory, unspecified.' '1x the sound of someone walking up the stairs.' Bay 4 is sealed, humming, and, according to the manifest, now full.",
  choices: [
    {
      label: "Open Bay 4",
      outcomes: [
        { chance: 0.5, text: "Wet salt air. A quiet tide, no bigger than a bath, rolling gently on the deck. The crew drinks some. It tastes like a place. Afterward it is a bath. Afterward it is gone, and the manifest crosses itself out.",
          effects: { water: 18, oxygen: 6 }, repair: { morale: 6 } },
        { chance: 0.5, text: "Four memories flood out, unspecified, and they are not yours. For a day, each of you has a childhood that isn't. {crew} remembers a dog they never had, and grieves it.",
          damage: { morale: -12 }, delayDays: 1 }
      ]
    },
    {
      label: "Leave it sealed",
      detail: "Cargo you didn't load isn't cargo.",
      outcomes: [
        { chance: 0.8, text: "The hum goes on for days. The manifest adds a line: '1x patience.' It is the only thing in the bay that is not leaking.",
          damage: { morale: -4 } },
        { chance: 0.2, text: "The bay opens itself at ship's midnight. A single stair-step, somewhere deep in the ship, clacks as someone walks up it. Nobody leaves their bunks for a day.",
          damage: { morale: -10, sensors: -5 } }
      ]
    },
    {
      label: "Jettison Bay 4 whole",
      cost: { parts: -2 },
      requires: { resources: { parts: 2 } },
      outcomes: [
        { chance: 0.85, text: "A bolt, a fuse, and the bay sails away turning slowly. For a moment every light on the ship points toward it. Then it is a speck, and a speck, and a speck, and the manifest is clean.",
          damage: { morale: -3 } },
        { chance: 0.15, text: "The bay does not drift. It follows, a polite distance back, for two days. Then it falls behind, and you are not sure which of you was disappointed.",
          damage: { morale: -8, engines: -5 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "odd-005",
  title: "Down Is a Suggestion",
  category: "bizarre",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "Water runs uphill. Not dramatically: the galley tap drips toward the ceiling, soup creeps out of the bowl and up the wall, and tears, {crew} reports, taste of salt, but go the wrong way. Gravity still works on everything except liquids, which have opinions.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Contain it — buckets, tape, patience",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.7, text: "A day of improvised plumbing. The water is mostly saved, and the ceiling is a seascape. By evening the effect fades, and the water drops, all at once, onto everyone.",
          effects: { water: -4 }, damage: { morale: -3 }, delayDays: 1 },
        { chance: 0.3, text: "You catch it. You catch most of it. The rest finds its way into the ducting, where it sits in the dark and slowly goes green.",
          effects: { water: -12 }, damage: { lifeSupport: -10, morale: -3 }, delayDays: 1 }
      ]
    },
    {
      label: "Drink as much as you can, now",
      detail: "Waste not, drown not.",
      outcomes: [
        { chance: 0.6, text: "A very silly afternoon. The crew drinks in the air like fish. {crew} wins a contest nobody planned. The tanks drain, the effect lifts, and everybody is, for once, thoroughly hydrated.",
          repair: { morale: 8 }, effects: { water: -14 } },
        { chance: 0.4, text: "You drink. The water does not stop. For an hour, you cannot be sure which direction is down, and neither can your stomach.",
          damage: { morale: -6 }, effects: { water: -14, meds: -1 } }
      ]
    },
    {
      label: "Wait it out",
      outcomes: [
        { chance: 0.5, text: "It passes in six hours with a long sigh through the pipes. A lot of towels are used. Not a drop is lost, which is the strange part.",
          damage: { morale: -3 } },
        { chance: 0.5, text: "It passes. The water, on its way down, takes a long detour through the electronics. Things hiss. Things smell of rain.",
          damage: { generator: -10, sensors: -5 }, effects: { water: -6 } }
      ]
    }
  ]
},
{
  id: "odd-006",
  title: "The Count",
  category: "bizarre",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "Every hour, a soft voice on the hull channel counts the crew. 'Seven.' It is always seven. There are not seven of you. It sounds pleased, in the way of a person who has finally been given the right amount of change.",
  requires: { resourceAbove: { crew: 1 } },
  choices: [
    {
      label: "Correct it politely",
      detail: "The count is wrong. Say so.",
      outcomes: [
        { chance: 0.6, text: "'Understood,' says the voice, and the count changes to the true number. It does not sound disappointed. It sounds like someone doing a long sum again, very carefully.",
          repair: { morale: 3 } },
        { chance: 0.4, text: "'Seven,' it says again, sadly, and the next time you look, there is a name on the bunk list you do not recognize. Everyone swears they have always known them.",
          effects: { crew: 1, food: -8 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Count with it",
      outcomes: [
        { chance: 0.7, text: "You count seven. It is delighted. A seventh seat at mess is filled with a coat and a quiet, grateful hush. Nothing happens. The coat is warm. The air, for no reason, is a little richer.",
          repair: { morale: 10 }, effects: { food: -4, oxygen: 6 } },
        { chance: 0.3, text: "You count seven. Seven is the number. At ship's midnight, {crew}'s bunk is empty and the voice says 'Seven' once more, quietly, and very relieved.",
          damage: { morale: -12 }, crewDeath: true }
      ]
    },
    {
      label: "Jam the channel",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.8, text: "The counting stops. For three hours, it is the best silence you have ever had. Then it starts again, from inside the walls, and it is much closer.",
          damage: { morale: -8, sensors: -5 } },
        { chance: 0.2, text: "The jam works. The count stops. Somewhere, in the dark, a very patient thing makes a note.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "odd-007",
  title: "Two Suns",
  category: "bizarre",
  weight: 0.7,
  zones: ["belt", "outer", "deep"],
  text: "Sol is where it should be, aft and small. Sol is also, unmistakably, dead ahead, the same size, the same age, throwing a second set of shadows across the bridge. Your astrogation console has quietly declined to take a side.",
  choices: [
    {
      label: "Trust the aft sun",
      outcomes: [
        { chance: 0.55, text: "The forward sun flickers and, as if embarrassed, goes out. Your course is unchanged. Navigation exhales. The bridge, for a moment, has no shadows at all.",
          damage: { morale: -3 } },
        { chance: 0.45, text: "The forward sun was the true one. You find out after a day and a half of confident error, spent a lot of fuel, and learned a lot about humility.",
          effects: { fuel: -12 }, damage: { astrogation: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Steer toward the forward sun",
      detail: "It is where you are going.",
      outcomes: [
        { chance: 0.4, text: "It is close, and warm, and it hums your own course back at you in a kind voice. You burn gently toward it. It dims as you approach, politely, like a host dimming lights.",
          repair: { astrogation: 10 }, effects: { fuel: -4 } },
        { chance: 0.6, text: "It recedes as you approach. For a day and a half you chase a star that will not be caught. The engines complain. The crew quietly resents the sun.",
          effects: { fuel: -10 }, damage: { engines: -8, morale: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "Navigate by the stars alone",
      detail: "Neither sun is on your side.",
      cost: { power: -4 },
      outcomes: [
        { chance: 0.7, text: "Old, slow, honest navigation. Nobody likes it. It works. The two suns drift together and merge into one, with a small, sheepish pop.",
          delayDays: 1, repair: { astrogation: 6 } },
        { chance: 0.3, text: "You pick the wrong set of stars. They are very close to the right ones, and very far from it. A fuel bill arrives in the form of a large hole in the tanks.",
          effects: { fuel: -10 }, damage: { astrogation: -10 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "odd-008",
  title: "A Table for Six",
  category: "bizarre",
  weight: 0.7,
  zones: ["outer", "deep"],
  requires: { resourceAbove: { crew: 2 } },
  text: "A long table drifts out of the dark, laid for dinner: candles that burn without air, wine that stays in its glasses, and place cards in careful script. {crew}. {crew2}. You. A chair is pulled out. The food is steaming. Nobody has set a table in sixty years.",
  choices: [
    {
      label: "Sit and eat",
      detail: "It's been so long since a real dinner.",
      outcomes: [
        { chance: 0.5, text: "Roast something, honestly good. A toast is made by someone who is not there. The crew leaves full and quiet and a little sad, with pockets full of bread. Bread that is, in the morning, still warm.",
          effects: { food: 20, water: 6 }, repair: { morale: 10 } },
        { chance: 0.5, text: "Delicious. Truly delicious. By dessert, each of you has noticed that you have been eating for several hours, in a ship's day that has not moved. You wake in your seats, with sauce on your sleeves, famished.",
          effects: { food: -10 }, damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Take the silverware and go",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.6, text: "A quick grab, a quick cut, an embarrassing tangle of candlesticks. Most of it is good steel. The table sighs in disappointment, and a little knife-shaped hole opens in your hull on the way out.",
          effects: { parts: 3 }, damage: { hull: -8, morale: -4 } },
        { chance: 0.4, text: "A hand comes out of the dark and returns each item to its place, one by one, with great gentleness. Then it points at you, once. Then at the door.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Burn past it",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.85, text: "The table recedes. Behind you, a single candle goes out, and a place card turns face-down. The crew eats paste in silence for a day. Not a word is said about bread.",
          damage: { morale: -6 } },
        { chance: 0.15, text: "You burn. The table is where you are anyway, a little further on, with another card: one with your name written differently.",
          damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "odd-009",
  title: "It Rains Indoors",
  category: "bizarre",
  weight: 0.7,
  text: "At 0400, a warm rain begins to fall in the galley. Soft, salty, steady. It does not come from the vents. The ceiling is dry. The rain simply happens, between the table and the floor, as if the room were remembering weather. Someone has opened an umbrella and is being brave about it.",
  choices: [
    {
      label: "Collect it",
      cost: { parts: -1 },
      requires: { resources: { parts: 1 } },
      outcomes: [
        { chance: 0.7, text: "Basins, tarps, a very impressive pan. A full day of rain, and the tanks gain a lot. It tastes, if you let it, of a childhood afternoon. You are not ashamed of any of it.",
          effects: { water: 20 }, repair: { morale: 6 }, delayDays: 1 },
        { chance: 0.3, text: "The rain is mildly saline. By evening, half the crew has a headache and the other half has a rash. The tanks gain a lot of water that you will have to boil twice.",
          effects: { water: 12, meds: -2 }, damage: { morale: -3 }, delayDays: 1 }
      ]
    },
    {
      label: "Let it fall",
      detail: "Maybe it needed to.",
      outcomes: [
        { chance: 0.7, text: "The crew stands in the galley with their faces up, and something loosens. It stops at noon, like a good thing that was never meant to last. The floor is dry in twenty minutes. The morale does not dry out for days.",
          repair: { morale: 12 } },
        { chance: 0.3, text: "It rains for two days. By the second the floor is a pond and the electronics hiss in protest. A very small fish is found, alive, in a coffee cup, and named.",
          repair: { morale: 6 }, damage: { generator: -10, sensors: -5 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "odd-010",
  title: "The Hand",
  category: "bizarre",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "It is three kilometers across, and it is a hand. Dark metal, four fingers curled over the thumb, palm up and open, the way you would hold something small to look at it. Your ship would fit in the palm with room to spare. The fingers are, very slowly, uncurling.",
  choices: [
    {
      label: "Fly into the palm",
      detail: "It's open. It's inviting. It's a hand.",
      outcomes: [
        { chance: 0.4, text: "Warmth. A gentle weight. The hand cups the ship and holds it for a day, while something very large studies the hull with great, soft care. It sets you down carefully, forty thousand kilometers closer to where you were going.",
          effects: { fuel: 14 }, repair: { hull: 10, morale: 4 }, delayDays: 1 },
        { chance: 0.6, text: "The hand closes, slowly, almost tenderly, around the ship. You are inside a fist for four days. When it opens, you are fine, and your hull is not.",
          damage: { hull: -20, morale: -8 }, delayDays: 4 }
      ]
    },
    {
      label: "Go around, wide",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 0.85, text: "A long, slow curve around a thing you do not understand. The fingers, behind you, uncurl the rest of the way and, after a long moment, wave.",
          damage: { morale: -3 } },
        { chance: 0.15, text: "You pass the thumb. The thumb moves, with the idle indifference of a thing scratching an itch. It misses you by forty meters and your hull still sings for a day.",
          damage: { hull: -10, engines: -5 } }
      ]
    },
    {
      label: "Hail it",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.5, text: "A long, low tone, like a held note on an organ, and a single finger rises in what you decide is greeting. You decide the tone means 'safe travels.' You are happy to believe that.",
          repair: { morale: 10 } },
        { chance: 0.5, text: "The tone does not answer. Instead the palm, very slowly, turns over, and the fingers curl closed on nothing, like someone who has remembered a thing they left on.",
          damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "odd-011",
  title: "Lost and Found",
  category: "bizarre",
  weight: 0.7,
  requires: { resourceAbove: { crew: 1 } },
  text: "The airlock fills, overnight, with everything anyone aboard has ever lost: a wedding ring, a left glove, an apology, a very small shoe. They are stacked neatly, each tagged. {crew}'s tag reads: 'the thing you meant to say.' It is a folded piece of paper.",
  choices: [
    {
      label: "Return the items to the crew",
      outcomes: [
        { chance: 0.7, text: "Rings and gloves go back to hands. {crew} reads their paper, folds it, and says nothing for two days. At the end of that time they are different, and gentler.",
          repair: { morale: 10 }, delayDays: 1 },
        { chance: 0.3, text: "A few of them were lost on purpose. The tags are very honest. There is an argument, and a door slam, and by dinner two people who were not speaking are both very quiet.",
          damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Recycle the physical items for parts",
      detail: "Gold and wiring are gold and wiring.",
      outcomes: [
        { chance: 0.8, text: "A pile of small, sad metal becomes a handful of useful things. Nobody comes to ask where their ring went. They know. They do not ask.",
          effects: { parts: 3 }, damage: { morale: -10 } },
        { chance: 0.2, text: "The recycler stops halfway, chews on an apology, and spits it out in pieces. It takes a day to unclog and a lot of very careful cleaning.",
          effects: { parts: 1 }, damage: { morale: -10, generator: -5 }, delayDays: 1 }
      ]
    },
    {
      label: "Leave it sealed",
      outcomes: [
        { chance: 1, text: "The airlock sits full for the rest of the trip. Nobody opens it. Everyone, at one time or another, goes and stands outside the door.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "odd-012",
  title: "The Tourist From Thursday",
  category: "bizarre",
  weight: 0.7,
  zones: ["outer", "deep"],
  text: "A small, cheerful figure steps out of an airlock that was not open and says, in your own voice, slightly hoarse: 'I'm from Thursday. Three days out. I can only say one thing, and I cannot choose what, and I've forgotten the rest.' It beams. It has a sunburn. In space.",
  choices: [
    {
      label: "Ask it for a warning",
      outcomes: [
        { chance: 0.5, text: "'Don't trust the left coil,' it says, and vanishes mid-sentence, like a coin down a drain. You check the left coil. It was about to fail. You fix it. You think you hear a very faint 'thanks.'",
          repair: { generator: 12 }, effects: { parts: -1 } },
        { chance: 0.5, text: "'Don't eat the soup,' it says, and winks out of existence. The soup is fine. The soup is perfectly fine. You skip it, on principle, and spend a day hungry and haunted.",
          effects: { food: -8 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Ask it for good news",
      outcomes: [
        { chance: 0.6, text: "'We make it,' it says, with a gleam, and is gone. You do not believe it. You also cannot stop believing it. The crew works harder for a week, on no evidence at all.",
          repair: { morale: 10 } },
        { chance: 0.4, text: "'We make it,' it says, and then, unwillingly: 'Not all of us.' It vanishes before you can ask. The whole crew heard it. Nobody sleeps well for a week.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Put it in the brig until Thursday",
      detail: "Fair is fair.",
      outcomes: [
        { chance: 0.7, text: "It is an excellent prisoner. By Wednesday it has taught the crew a card game that cannot be lost. On Thursday it shakes everybody's hand, steps into the airlock, and is not seen again. The airlock was never open.",
          repair: { morale: 6 }, effects: { food: -4 }, delayDays: 1 },
        { chance: 0.3, text: "It is, on Thursday, already gone, and so is an ordinary cell door, and so is a day of yours. The brig has a faint, pleased smell, like warm sunburn.",
          damage: { morale: -5 }, delayDays: 2 }
      ]
    }
  ]
}
);
