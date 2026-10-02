/* ORION TRAIL — crew drama & morale. Prefix: crew- */
SCENARIOS.push(
{
  id: "crew-001",
  title: "The Hoarder",
  category: "crew-drama",
  text: "A crate of protein bars turns up under a deck plate, squirreled away with someone's name written inside the lid. The name belongs to someone still alive. The crew has noticed.",
  choices: [
    {
      label: "Confiscate and redistribute, publicly",
      outcomes: [
        { chance: 0.7, text: "Justice, served with protein bars. The hoarder eats alone for a week but the ship feels cleaner.",
          effects: { food: 8 }, repair: { morale: 8 } },
        { chance: 0.3, text: "The hoarder makes a speech about 'preparedness' that lands better than expected. Now everyone's counting everyone's bites.",
          effects: { food: 8 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Quietly return it and say nothing",
      detail: "Mercy, or cowardice with better posture",
      outcomes: [
        { chance: 1, text: "No scene, no stockpile, no trust either. The crew knows you know. You know they know you know.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "Make hoarding legal — personal ration reserves for all",
      outcomes: [
        { chance: 0.5, text: "Oddly, it works. People guard their little stashes like heirlooms and stop watching each other's plates.",
          effects: { food: -6 }, repair: { morale: 10 } },
        { chance: 0.5, text: "Everyone immediately over-withdraws. The commons empties like a bar at closing time.",
          effects: { food: -14 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "crew-002",
  title: "The Stowaway",
  category: "crew-drama",
  text: "Deck four's 'haunted' vent turns out to contain a nineteen-year-old dock rat who boarded at Luna with a duffel bag and a plan that ended at the duffel bag. She's been living on condensation and stolen crackers. She can weld, she claims. Everyone can weld, they claim.",
  choices: [
    {
      label: "Put her on the roster, full share",
      outcomes: [
        { chance: 0.7, text: "She can, in fact, weld. Better than the person whose job it was. That person is now 'reassigned' and furious.",
          effects: { crew: 1, food: -8 }, repair: { hull: 6 } },
        { chance: 0.3, text: "She cannot weld. She can, however, eat, and does so at professional volume.",
          effects: { crew: 1, food: -10 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Confine her to quarters until the next waypoint",
      detail: "Eats like crew, works like cargo",
      outcomes: [
        { chance: 1, text: "She sits in a locked cabin costing calories and generating nothing but guilt. The crew slides food under her door and looks at you.",
          effects: { food: -6 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Half rations, probationary status",
      outcomes: [
        { chance: 0.5, text: "She takes the deal without blinking. Kid's eaten worse deals. She pulls double shifts to prove the point.",
          effects: { crew: 1, food: -4 }, repair: { morale: 4 } },
        { chance: 0.5, text: "Half rations, it turns out, means half from the manifest and half from wherever. Stores come up short.",
          effects: { crew: 1, food: -12 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "crew-003",
  title: "The Two Heavens",
  category: "crew-drama",
  text: "Three candidate planets, one scripture. Half the mess now believes Gliese is the Promised Ground; the other half is certain God meant Tau Ceti and always did. Last night the Keplerites — both of them — found their prayer mats spaced. It is becoming a staffing issue.",
  choices: [
    {
      label: "Declare the ship secular until landfall",
      outcomes: [
        { chance: 0.6, text: "Grudging peace. Services move to private bunks and pointed silences. Nobody spaces anything else.",
          repair: { morale: 6 } },
        { chance: 0.4, text: "Both factions instantly agree on one theological point: you are the problem. Ecumenism, achieved.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Host a formal debate, captain moderating",
      cost: { power: -4 },
      outcomes: [
        { chance: 0.5, text: "Three hours, actual citations, zero punches. Theology Night becomes a weekly fixture and the best entertainment aboard.",
          repair: { morale: 16 } },
        { chance: 0.5, text: "It goes to fists during the rebuttal round. The doctor bills you for two split lips and one bitten ear.",
          effects: { meds: -2 }, damage: { morale: -12 } }
      ]
    },
    {
      label: "Endorse a Promised Ground yourself",
      detail: "Pick a side. Captains don't get to be agnostic",
      outcomes: [
        { chance: 0.5, text: "Your faction is delighted. The other faction develops a rich oral tradition about captains who chose wrong.",
          repair: { morale: 12 } },
        { chance: 0.5, text: "You backed the wrong prophet. Half the ship now prays, loudly, for your enlightenment. During your shifts.",
          damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "crew-004",
  title: "The Night Watch",
  category: "crew-drama",
  text: "Navigation's night watch has become a two-person honeymoon. Torres and Okafor log every shift together, check each other's math with foreheads touching, and missed a debris ping Tuesday because — per the log — 'sensor glare.' The rest of the rotation has filed a complaint with footnotes.",
  choices: [
    {
      label: "Split them onto opposite shifts",
      outcomes: [
        { chance: 0.7, text: "The watches sharpen immediately. Two people are heartbroken on a precise twelve-hour rotation, which is at least efficient.",
          repair: { astrogation: 6 }, damage: { morale: -6 } },
        { chance: 0.3, text: "Okafor requests transfer to the galley in protest. Now navigation is short-handed and breakfast is bitter.",
          damage: { morale: -10, astrogation: -5 } }
      ]
    },
    {
      label: "Leave them, add a third watchstander",
      detail: "Chaperone duty, the worst duty",
      outcomes: [
        { chance: 0.6, text: "The third wheel catches everything the lovebirds miss and develops a martyr complex the whole ship enjoys.",
          repair: { morale: 6 }, damage: { astrogation: -4 } },
        { chance: 0.4, text: "The third watchstander falls for Torres. The geometry of the night shift becomes non-Euclidean.",
          damage: { morale: -10 } }
      ]
    },
    {
      label: "Marry them at the next waypoint",
      cost: { food: -8 },
      outcomes: [
        { chance: 0.7, text: "A wedding. An actual wedding, with ration-loaf cake and someone crying into a spanner. The ship glows for a week.",
          repair: { morale: 14 } },
        { chance: 0.3, text: "Engaged three days, then a breakup audible through two bulkheads. The cake was already eaten. No refunds.",
          damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "crew-005",
  title: "The House Always Wins",
  category: "crew-drama",
  text: "There is a casino in aft storage. Rodriguez runs it: cards, dice, a wheel made from a fan housing. The currency is ration chits, and the house is up three weeks of somebody's meals. Two crew are quietly starving, and one of them is your best mechanic.",
  choices: [
    {
      label: "Shut it down, void all debts",
      outcomes: [
        { chance: 0.6, text: "The gamblers grumble; the debtors eat. Rodriguez surrenders the chit box with the dignity of a deposed monarch.",
          effects: { food: 6 }, damage: { morale: -5 } },
        { chance: 0.4, text: "The casino goes underground, minus your oversight and plus loan-sharking. The vig is now payable in dessert.",
          effects: { food: -8 }, damage: { morale: -6 } }
      ]
    },
    {
      label: "Regulate it — table caps, no ration stakes",
      outcomes: [
        { chance: 0.5, text: "Gambling for chore-swaps and bragging rights turns out to be exactly as fun and half as fatal. Rodriguez thrives.",
          repair: { morale: 8 } },
        { chance: 0.5, text: "A black-market side game opens for the serious players. Same wheel, worse odds, your rules cited as the appetizer.",
          effects: { food: -6 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Sit down and play Rodriguez for the debts",
      detail: "Winner takes the ledger",
      outcomes: [
        { chance: 0.5, text: "You clean him out in front of everyone and burn the ledger at the table. Legends are cheaper than rations and worth more.",
          effects: { food: 10 }, repair: { morale: 8 } },
        { chance: 0.5, text: "Rodriguez deals like a machine and smiles like a lawyer. You now owe him your dessert until Titan, publicly.",
          effects: { food: -10 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "crew-006",
  title: "The Cartographer of Corridor C",
  category: "crew-drama",
  text: "Nakamura hasn't spoken in nine days. Instead she paints — corridor C, deck to ceiling, star charts in stolen hull sealant. The doctor says trauma. The navigator says, very quietly, 'more accurate than ours.' She has mapped the Kuiper approach from memory. She has never seen the Kuiper approach.",
  choices: [
    {
      label: "Give her the sealant and the corridor",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.7, text: "The navigator starts photographing the walls and correcting the plot from them. Nobody discusses this in the log.",
          repair: { astrogation: 10, morale: 4 } },
        { chance: 0.3, text: "She finishes corridor C and starts, without pausing, on the life support housing. The sealant goes where sealant shouldn't.",
          damage: { lifeSupport: -8 } }
      ]
    },
    {
      label: "Treat her — sedation, therapy, the works",
      cost: { meds: -2 },
      outcomes: [
        { chance: 0.6, text: "On day four she says 'the brushes are terrible' and then keeps talking. She paints on requisitioned scrap now. Off duty.",
          repair: { morale: 6 } },
        { chance: 0.4, text: "She stops painting. She also stops eating with the crew, and mostly stops. The corridor stays half-charted, like her.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Paint it over, order her back to duty",
      outcomes: [
        { chance: 1, text: "The corridor is grey again. So is she. Within a week the crew has pencilled tiny stars back in, low on the walls, in protest.",
          damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "crew-007",
  title: "Whisper Campaign",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"], forbidsFlag: "mutiny_brewing" },
  text: "You find it taped inside a ration locker: the duty roster, rewritten, with someone else's name where yours goes. Third one this week. The mess goes quiet when you walk in now — the specific quiet of people who were just talking about you.",
  choices: [
    {
      label: "Call all-hands and address it head-on",
      outcomes: [
        { chance: 0.6, text: "You read the fake roster aloud, badly, doing the voices. Laughter breaks something loose. The quiet lifts.",
          repair: { morale: 10 } },
        { chance: 0.4, text: "Your speech about unity confirms every fear it was meant to soothe. The next roster has a date on it.",
          damage: { morale: -8 }, flag: "mutiny_brewing" }
      ]
    },
    {
      label: "Ignore it, project calm",
      detail: "Captains don't chase paper",
      outcomes: [
        { chance: 0.5, text: "The notes stop. Either it was a joke, or someone is done practicing. You choose to believe the first thing.",
          repair: { morale: 4 } },
        { chance: 0.5, text: "The quiet spreads to engineering. People stop meeting your eyes and start meeting each other's.",
          damage: { morale: -6 }, flag: "mutiny_brewing" }
      ]
    },
    {
      label: "Hunt down the author",
      outcomes: [
        { chance: 0.4, text: "Locker logs point to one very surprised comms tech, who folds instantly — and surrenders the ration stash he'd been building 'for after.' Mostly a joke. Mostly.",
          effects: { food: 6 }, damage: { morale: -5 } },
        { chance: 0.6, text: "You accuse the wrong person, publicly. The right person watches you do it, and starts recruiting.",
          damage: { morale: -12 }, flag: "mutiny_brewing" }
      ]
    }
  ]
},
{
  id: "crew-008",
  title: "Fists in the Mess",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"] },
  text: "It starts over the last bread roll and ends with two crew in medbay and a table bolted at a new angle. Nobody remembers who swung first. Everybody remembers whose side they took. The mess has split, unofficially, and now eats in shifts to avoid itself.",
  choices: [
    {
      label: "Dock both their rations, by the book",
      outcomes: [
        { chance: 0.6, text: "Order, restored. The book exists for a reason and the reason has a black eye. Both apologize through gritted teeth.",
          effects: { food: 4 }, repair: { morale: 5 } },
        { chance: 0.4, text: "You have created two martyrs, each with a fan club. Their tables now compete at everything, loudly.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Punish nobody — open the good stores tonight",
      cost: { food: -8 },
      outcomes: [
        { chance: 0.7, text: "Hard to hold a grudge over actual cheese. By the second helping the combatants are comparing bruises like fishing stories.",
          repair: { morale: 8 } },
        { chance: 0.3, text: "Free food after a brawl reads as a reward for brawling. Three more 'disagreements' book themselves by week's end.",
          effects: { meds: -1 }, damage: { morale: -4 } }
      ]
    },
    {
      label: "Sentence them to repair the table. Together",
      outcomes: [
        { chance: 0.5, text: "Four hours under a table with one wrench forges a friendship built entirely on shared hatred of the table. It holds.",
          repair: { morale: 8 } },
        { chance: 0.5, text: "Round two, under the table, with the wrench. The doctor doesn't even look up when they come in.",
          effects: { meds: -2 }, damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "crew-009",
  title: "The Sick-Out",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"] },
  text: "Six crew report 'ear pressure' the same morning, symptoms identical down to the wording. The doctor examines all six and writes one word on the chart: nothing. Engineering runs a skeleton shift, the scrubbers need cleaning, and everyone's ears feel fine at mealtimes.",
  choices: [
    {
      label: "Call the bluff — order them to stations",
      outcomes: [
        { chance: 0.5, text: "They go, ears miraculously cured, resentment fully intact. The work gets done at exactly minimum specification.",
          damage: { morale: -6 } },
        { chance: 0.5, text: "They dig in. The scrubbers go another week uncleaned, and the air starts tasting like a locker room's opinion of you.",
          damage: { morale: -6, lifeSupport: -10 } }
      ]
    },
    {
      label: "Declare a ship-wide rest day",
      detail: "If you can't beat the plague, schedule it",
      outcomes: [
        { chance: 0.7, text: "The epidemic resolves overnight, as epidemics of this kind do. People return to work embarrassed and rested.",
          repair: { morale: 10 }, delayDays: 1 },
        { chance: 0.3, text: "One rest day metastasizes into an unofficial long weekend. The galley gets raided in a spirit of celebration.",
          effects: { food: -6 }, repair: { morale: 4 }, delayDays: 2 }
      ]
    },
    {
      label: "Offer bonus rations per shift worked",
      cost: { food: -8 },
      outcomes: [
        { chance: 0.6, text: "Six pairs of ears recover within the hour. Capitalism: undefeated in a vacuum.",
          repair: { morale: 5 } },
        { chance: 0.4, text: "It works, and now every unpleasant job aboard has a price tag. You have invented wages. They have invented strikes.",
          effects: { food: -6 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "crew-010",
  title: "The Petition",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"] },
  text: "A petition arrives, signed by half the ship: increased rations until the next waypoint, or a formal work slowdown. The signatures are in careful alphabetical order, which means someone organized this, which means someone aboard can organize things. That's the part that keeps you up.",
  choices: [
    {
      label: "Grant it in full",
      cost: { food: -15 },
      outcomes: [
        { chance: 0.7, text: "Full bellies, full shifts, and the organizer nods at you in the corridor like a fellow professional.",
          repair: { morale: 12 } },
        { chance: 0.3, text: "The crew learns petitions work. A second one arrives within days, formatting improved, demands enlarged.",
          effects: { food: -6 }, repair: { morale: 5 } }
      ]
    },
    {
      label: "Refuse, flatly",
      outcomes: [
        { chance: 0.5, text: "Respect for spine is a real currency out here. The slowdown never materializes; the organizer starts eating alone.",
          repair: { morale: 4 } },
        { chance: 0.5, text: "The slowdown is meticulous. Every task done exactly to spec, exactly at speed, exactly forever. You lose days to compliance.",
          damage: { morale: -8 }, delayDays: 2 }
      ]
    },
    {
      label: "Counter-offer: half now, half at the waypoint",
      cost: { food: -8 },
      outcomes: [
        { chance: 0.6, text: "Deals get honored out here or nothing works. Both sides pretend this was the plan all along.",
          repair: { morale: 6 } },
        { chance: 0.4, text: "You split the difference and please exactly no one. The phrase 'half-measures' enters the ship's vocabulary, aimed at you.",
          damage: { morale: -6 } }
      ]
    }
  ]
},
{
  id: "crew-011",
  title: "Two Turns Loose",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"] },
  text: "The generator access panel comes off in the engineer's hand — every bolt backed out precisely two turns. Not vibration. Fingers. Somebody with a wrench and a grievance has been expressing themselves on your power plant, gently, like a first warning. The next message may carry more torque.",
  choices: [
    {
      label: "Post watches on engineering around the clock",
      outcomes: [
        { chance: 0.7, text: "Nothing else loosens. But standing guard over your own crewmates changes what kind of ship this is, and everyone feels it.",
          damage: { morale: -5 } },
        { chance: 0.3, text: "Engineering is covered, so the message moves to the hull, in the form of a mysteriously weeping seam.",
          damage: { hull: -10, morale: -5 } }
      ]
    },
    {
      label: "Announce an amnesty and a grievance box",
      outcomes: [
        { chance: 0.6, text: "Forty-one complaints, one anonymous confession, zero charges filed. The bolts stay tight. Some of the complaints are even fair.",
          repair: { morale: 8 } },
        { chance: 0.4, text: "The box gets a written reply: every bolt on the coolant manifold, four turns this time. The grievance is you.",
          damage: { generator: -10, morale: -4 } }
      ]
    },
    {
      label: "Re-torque everything and say nothing",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.7, text: "It never happens again. Whoever it was said their piece and got it out of their system. Probably. You check weekly anyway.",
          repair: { morale: 2 } },
        { chance: 0.3, text: "It was a warning, and you ignored it. The generator drops offline mid-shift with six fasteners missing entirely.",
          damage: { generator: -10, morale: -6 } }
      ]
    }
  ]
},
{
  id: "crew-012",
  title: "The Long Look",
  category: "crew-drama",
  requires: { systemDamaged: ["morale"] },
  text: "Chen has been sitting in the open inner airlock for six hours. Suit off. Not touching anything — just looking at the outer door the way you look at an exit. The crew takes turns not-mentioning it to you. The doctor uses the word 'ideation' and then waits.",
  choices: [
    {
      label: "Sit with him. All night if it takes",
      outcomes: [
        { chance: 0.8, text: "At 0400 he asks whether the galley still burns the oatmeal. You say yes. He says good, some things should stay the same, and stands up.",
          repair: { morale: 8 }, delayDays: 1 },
        { chance: 0.2, text: "He talks until morning, then reports for duty, hollow-eyed and polite. Whatever he came to the airlock to decide, it isn't decided.",
          damage: { morale: -4 }, delayDays: 1 }
      ]
    },
    {
      label: "Have the doctor sedate him, mandatory watch",
      cost: { meds: -3 },
      outcomes: [
        { chance: 0.7, text: "He sleeps fourteen hours and wakes up angry, which the doctor says is an improvement over empty. The ship exhales.",
          repair: { morale: 4 } },
        { chance: 0.3, text: "He wakes up furious at being managed instead of heard. Now he's watched around the clock and speaks to no one who does the watching.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Give him space — quietly lock out the airlock controls",
      detail: "Respect the vigil, disable the exit",
      outcomes: [
        { chance: 0.7, text: "He sits three more nights, then stops coming. A month later you find a thank-you note in your logbook, unsigned. You know the handwriting.",
          damage: { morale: -6 } },
        { chance: 0.3, text: "He knew the override. He was in engineering four years. The alarm wakes the whole ship and there is nothing outside to recover.",
          crewDeath: true, damage: { morale: -20 } }
      ]
    }
  ]
},
{
  id: "crew-013",
  title: "The Quiet Arsenal",
  category: "crew-drama",
  requires: { requiresFlag: "mutiny_brewing" },
  text: "Weapons inventory comes up short: two shock batons and a cutting torch, signed out to a maintenance job that doesn't exist. The quartermaster's hands shake as she shows you the ledger. Whoever rewrote your duty roster is now editing your armory. The whispers have footsteps.",
  choices: [
    {
      label: "Lock down the armory and search every cabin",
      outcomes: [
        { chance: 0.5, text: "The batons turn up in a duct, wiped clean. Nobody steps forward. The searches leave every cabin tidy and every trust untidied.",
          damage: { morale: -8 } },
        { chance: 0.5, text: "The searches find nothing except how it feels to be searched. Recruitment surges. The torch stays missing.",
          damage: { morale: -10 }, flag: "mutiny_open" }
      ]
    },
    {
      label: "Address the grievances publicly, restore ration levels",
      cost: { food: -10 },
      outcomes: [
        { chance: 0.6, text: "You name the problems before they can be demands. The footsteps stop. A shock baton reappears in the armory, unsigned, apologetic.",
          repair: { morale: 12 } },
        { chance: 0.4, text: "Concession reads as blood in the water. The demands that come back are longer, and the torch does not come back at all.",
          damage: { morale: -6 }, flag: "mutiny_open" }
      ]
    },
    {
      label: "Quietly arm the crew you trust",
      outcomes: [
        { chance: 0.4, text: "The plotters notice the sidearms at dinner and do the arithmetic. The ledger balances itself overnight. Nobody says why.",
          damage: { morale: -4 } },
        { chance: 0.6, text: "Now there are two armed crews on one hull, sorting themselves by mess table. You have organized your own opposition.",
          damage: { morale: -12 }, flag: "mutiny_open" }
      ]
    }
  ]
},
{
  id: "crew-014",
  title: "The Hour of the Wolf",
  category: "crew-drama",
  requires: { requiresFlag: "mutiny_open" },
  text: "They come at ship's midnight — five crew at your cabin door with a baton, a torch, and a typed list of demands nobody intends to read aloud. Ruiz is in front. Ruiz taught your kid chess in Luna orbit. 'Captain,' he says, 'don't make this a thing.'",
  choices: [
    {
      label: "Fight for your ship",
      outcomes: [
        { chance: 0.65, text: "Your loyalists arrive at a run and Ruiz's people fold — batons clatter, the torch scores a bulkhead, nobody bleeds much. Order returns wearing bruises.",
          repair: { morale: 12 }, damage: { hull: -10 } },
        { chance: 0.35, text: "In the dark, in the scrum, the torch finds someone. Nobody remembers ordering it. You hold the ship and lose the argument forever.",
          crewDeath: true, damage: { morale: -15 } }
      ]
    },
    {
      label: "Talk. All night, no guards, door open",
      outcomes: [
        { chance: 0.5, text: "By dawn there's a ship's council, elected, with real teeth. You keep the chair and lose the crown. The ship works better without it.",
          effects: { food: -10 }, repair: { morale: 15 } },
        { chance: 0.5, text: "Talks collapse at 0300. Ruiz holds engineering for two days until hunger votes no confidence in him. The damage is done in the dark.",
          damage: { morale: -10, generator: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Hand Ruiz the log codes",
      detail: "Let him find out what the chair weighs",
      outcomes: [
        { chance: 1, text: "Two days later he hands them back, grey-faced — turns out the chair is where the blame sits. You're captain again of a ship where everyone has seen the bottom.",
          damage: { morale: -25 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "crew-015",
  title: "One Hundred Days Out",
  category: "crew-drama",
  text: "Someone did the math: tomorrow is day one hundred. The crew wants a party — real food, the good lights, music on the ship-wide comm. The last party anyone attended was Earth's funeral. The doctor prescribes it. The quartermaster prices it. Both of them are correct.",
  choices: [
    {
      label: "Full celebration, lights and all",
      cost: { food: -10, power: -8 },
      outcomes: [
        { chance: 0.8, text: "Someone unearths a guitar. Someone else can actually sing. For one night the ship sounds like a place people live instead of a place people last.",
          repair: { morale: 15 } },
        { chance: 0.2, text: "It's wonderful until the toasts, when 'to absent friends' empties the room one memory at a time. Worth it. Probably.",
          repair: { morale: 4 } }
      ]
    },
    {
      label: "Modest observance — one candle, one hour",
      cost: { food: -4 },
      outcomes: [
        { chance: 1, text: "Cake without the c-word: ration loaf with a candle in it. Everyone gets a slice and a moment. It's enough. Barely, but enough.",
          repair: { morale: 5 } }
      ]
    },
    {
      label: "Skip it. Milestones don't fill tanks",
      outcomes: [
        { chance: 0.6, text: "The day passes unmarked. So, increasingly, do the crew's faces. You saved ten meals and spent something harder to inventory.",
          damage: { morale: -6 } },
        { chance: 0.4, text: "They throw it anyway — in the cargo bay, in secret, badly, with liberated stores. You weren't invited. That's the part that stings.",
          effects: { food: -6 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "crew-016",
  title: "The Last Coffee",
  category: "crew-drama",
  text: "The final coffee ration is weighed, photographed, and locked in the galley safe. Eleven grams. The crew has opinions: raffle it, split it into brown-adjacent water, or save it for landing day. Productivity has stopped ship-wide pending resolution. This is the real cargo now.",
  choices: [
    {
      label: "Raffle it — one winner, fair and square",
      outcomes: [
        { chance: 0.7, text: "The winner drinks it alone on the observation deck at sunrise-equivalent, and narrates. Nobody begrudges it. It becomes scripture.",
          repair: { morale: 6 } },
        { chance: 0.3, text: "The winner is the least-liked person aboard, who describes every sip. Conspiracy theories about the draw bloom by dinner.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "Seal it for landing day",
      detail: "A reason to live, eleven grams strong",
      outcomes: [
        { chance: 0.5, text: "The safe becomes a shrine. People touch it for luck going on shift. Morale accrues interest at eleven grams to the light-year.",
          repair: { morale: 14 } },
        { chance: 0.5, text: "The safe is empty within a month. No suspects, no evidence, eleven grams gone. Everyone smells everyone's breath for weeks.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Brew it now — one sip each, all hands",
      cost: { water: -2 },
      outcomes: [
        { chance: 1, text: "It's over in ninety seconds and tastes like the end of something. Still: everyone got some, and nobody has to guard a safe.",
          repair: { morale: 4 } }
      ]
    }
  ]
}
);
