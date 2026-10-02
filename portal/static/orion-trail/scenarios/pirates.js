/* ORION TRAIL — pirates & hostiles. Prefix: pir- */
SCENARIOS.push(
{
  id: "pir-001",
  title: "Belt Corsairs",
  category: "pirates",
  text: "A cutter unfolds from behind a tumbling rock, weapons hot. The hail is polite the way a knife is polite: 'Heave to. Cargo inspection. Survivors get to stay survivors.'",
  zones: ["belt", "outer"],
  choices: [
    {
      label: "Fight",
      detail: "They picked the wrong refugees",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.6, text: "Your point-defense stitches their engine block. The cutter limps off trailing regret. You salvage what falls off.",
          effects: { parts: 3 }, damage: { weapons: -10, hull: -8 }, repair: { morale: 8 } },
        { chance: 0.4, text: "They shoot back with feeling. You win, technically, in the sense that you are still moving.",
          damage: { hull: -25, weapons: -15 }, effects: { parts: 1 }, crewDeath: false }
      ]
    },
    {
      label: "Run for it",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 0.65, text: "You redline the drives and lose them in the clutter. The engines file a formal complaint.",
          damage: { engines: -8 } },
        { chance: 0.35, text: "They wing you twice before you shake them. Fast, but not free.",
          damage: { engines: -8, hull: -15 } }
      ]
    },
    {
      label: "Pay the toll",
      detail: "Pride is not a life-support gas",
      outcomes: [
        { chance: 0.8, text: "They take their cut with professional courtesy and even wave. The crew watches the food go, silent.",
          effects: { food: -15, fuel: -8, parts: -2 }, damage: { morale: -10 } },
        { chance: 0.2, text: "They take the toll, then take a little more, because who's going to stop them? Not you. That was the point of paying.",
          effects: { food: -20, fuel: -10, parts: -3, meds: -2 }, damage: { morale: -15 } }
      ]
    }
  ]
},
{
  id: "pir-002",
  title: "Scavenger Skiffs",
  category: "pirates",
  text: "Three skiffs rise out of the debris ring, hulls welded from hab-module scrap, guns that are mostly mining lasers and hope. The voice on the channel is young and cracking: 'Food and water. Please. Then we'll go.' The 'please' is new. The guns are not.",
  zones: ["inner"],
  choices: [
    {
      label: "Burn them down",
      detail: "They're armed. That's enough. It has to be.",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.75, text: "It takes eleven seconds. You pick usable plate off the wreckage while the channel stays very, very quiet.",
          effects: { parts: 2 }, damage: { morale: -12 } },
        { chance: 0.25, text: "The lead skiff rams you on the way down. Whoever they were, they meant it.",
          effects: { parts: 1 }, damage: { hull: -15, morale: -15 } }
      ]
    },
    {
      label: "Give them something",
      detail: "Feeding strangers, out here",
      cost: { food: -10 },
      outcomes: [
        { chance: 0.7, text: "They take the crates gently, like they might break. The young voice says thanks twice and cuts the channel before it cracks.",
          repair: { morale: 6 }, flag: "fed_skiffs" },
        { chance: 0.3, text: "The skiffs crowd in for seconds. You buy them off with more than you meant to and leave before generosity becomes salvage.",
          effects: { food: -8, water: -5 } }
      ]
    },
    {
      label: "Outrun them",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.8, text: "Their engines are as starved as they are. You pull away in minutes and try not to think about it.",
          damage: { engines: -5 } },
        { chance: 0.2, text: "A lucky mining laser scores your flank before you clear range. Even desperation shoots straight sometimes.",
          damage: { engines: -5, hull: -10 } }
      ]
    }
  ]
},
{
  id: "pir-003",
  title: "The Tithe Gate",
  category: "pirates",
  text: "Chained rocks and gun platforms string across the corridor like bad jewelry. Red Ledger clan colors on every barrel. The toll schedule broadcasts on loop — itemized, notarized, almost bureaucratic. Corsairs with paperwork: the belt's most successful idea.",
  zones: ["belt"],
  sensorWarning: "Gun platforms strung across the corridor ahead. Toll frequencies broadcasting.",
  choices: [
    {
      label: "Pay the tithe",
      detail: "It's posted. It's almost legal.",
      outcomes: [
        { chance: 0.85, text: "They stamp your hull with clan paint: PAID. It means safe passage. It also means everyone with a scope knows you're a payer.",
          effects: { food: -10, fuel: -10 }, damage: { morale: -8 }, flag: "paid_toll" },
        { chance: 0.15, text: "A 'processing surcharge' appears mid-transaction. You pay that too. The stamp goes on your hull. The word goes on ahead of you.",
          effects: { food: -12, fuel: -12, parts: -2 }, damage: { morale: -10 }, flag: "paid_toll" }
      ]
    },
    {
      label: "Run the gate",
      cost: { fuel: -12 },
      outcomes: [
        { chance: 0.55, text: "You thread the platforms at full burn. They score your paint and your nerves, not much else.",
          damage: { hull: -8 } },
        { chance: 0.45, text: "The gate guns are automated and patient. You get through wearing new holes.",
          damage: { hull: -22, engines: -10 } }
      ]
    },
    {
      label: "Shoot your way through",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.4, text: "You kill a platform and the gap you make is yours. Salvage tumbles free of the wreck.",
          effects: { parts: 3 }, damage: { weapons: -10, hull: -10 } },
        { chance: 0.35, text: "A slugging match with fixed guns. You win by leaving.",
          damage: { hull: -20, weapons: -15 } },
        { chance: 0.25, text: "The platforms are older than you and better at this. Someone doesn't make it to the med bay in time.",
          damage: { hull: -25, weapons: -20 }, crewDeath: true }
      ]
    }
  ]
},
{
  id: "pir-004",
  title: "Word Travels",
  category: "pirates",
  text: "Another clan cutter drops in close — easy, confident, no warning shot. Their spotter scopes the Red Ledger paid-stamp on your hull and laughs on the open channel: 'A payer! Word gets around, friend. Our rates are higher. Call it inflation.'",
  zones: ["belt", "outer"],
  requires: { requiresFlag: "paid_toll" },
  choices: [
    {
      label: "Pay the new rate",
      detail: "You've established a reputation. It's paying rent now.",
      outcomes: [
        { chance: 0.7, text: "They're right — you do understand how this works. The crew won't meet your eyes during the transfer.",
          effects: { food: -18, fuel: -12 }, damage: { morale: -14 } },
        { chance: 0.3, text: "Payers get the deluxe inspection. They find the med locker.",
          effects: { food: -22, fuel: -15, meds: -3 }, damage: { morale: -18 } }
      ]
    },
    {
      label: "Scrape the stamp off, guns out",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.6, text: "You burn the clan paint off with a maneuvering thruster while your turrets track them. Point made. They leave. Your capacitors hate you.",
          damage: { weapons: -8 }, repair: { morale: 12 } },
        { chance: 0.4, text: "They call it. It's short and ugly and you're still here at the end, with pieces of their boarding launch for souvenirs.",
          effects: { parts: 2 }, damage: { hull: -20, weapons: -12 } }
      ]
    },
    {
      label: "Run",
      cost: { fuel: -15 },
      outcomes: [
        { chance: 0.6, text: "Payers who run are an insult. They chase you longer than makes economic sense.",
          damage: { engines: -10 } },
        { chance: 0.4, text: "They wing you twice out of spite before breaking off.",
          damage: { engines: -10, hull: -15 } }
      ]
    }
  ]
},
{
  id: "pir-005",
  title: "The Refinery Queue",
  category: "pirates",
  text: "The refinery burns like a small sun, ringed by a warlord's fleet. Fuel for sale, says the broadcast, prices negotiable in whatever you love most. A tender detaches to inventory your assets. Its crew has the well-fed look nobody honest out here has.",
  zones: ["outer"],
  choices: [
    {
      label: "Barter hard",
      detail: "Parts and meds for fuel. Everything is currency.",
      cost: { parts: -4, meds: -3 },
      outcomes: [
        { chance: 0.7, text: "The quartermaster respects a ship that haggles. You leave lighter in the holds and heavier in the tanks.",
          effects: { fuel: 20 } },
        { chance: 0.3, text: "The delivery comes up short. The complaints department is a gun emplacement, so you log it and go.",
          effects: { fuel: 12 }, damage: { morale: -8 } }
      ]
    },
    {
      label: "Let them recruit a 'volunteer'",
      detail: "A full tank costs one signature. Not yours.",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.8, text: "Your engineer's mate signs the warlord's book for a full tank and a signing bonus. Nobody blames them out loud.",
          effects: { crew: -1, fuel: 30 }, damage: { morale: -12 } },
        { chance: 0.2, text: "The tender's captain does the choosing, not you. You take the fuel anyway, which is the part the crew remembers.",
          effects: { crew: -1, fuel: 30 }, damage: { morale: -20 } }
      ]
    },
    {
      label: "Pass by",
      outcomes: [
        { chance: 0.75, text: "You coast past the queue with your tanks light and your pride intact. Pride burns poorly in a fusion torch.",
          delayDays: 1, damage: { morale: -5 } },
        { chance: 0.25, text: "A patrol captain invents an 'exit corridor fee.' You are not in a position to appeal.",
          effects: { fuel: -8, food: -8 }, damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "pir-006",
  title: "Prize Crew",
  category: "pirates",
  text: "This one doesn't want cargo. The converted hauler runs prize crews for the warlord — they take whole ships and leave the owners at the refinery, working off air debt forever. The hail is one word: 'Stop.' There is no toll. You are the toll.",
  zones: ["outer"],
  sensorWarning: "Large contact matching course. Squawking prize-crew codes.",
  choices: [
    {
      label: "Fight",
      detail: "They want the ship intact. You don't have that problem.",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.45, text: "You put fire into their boarding bays until taking your ship stops being profitable. They break off. You even scoop up their dropped gear.",
          effects: { parts: 4 }, damage: { hull: -15, weapons: -12 } },
        { chance: 0.35, text: "You win the way you win out here: still moving, still breathing, needing a shipyard you will never see.",
          damage: { hull: -28, weapons: -18, morale: -10 } },
        { chance: 0.2, text: "The prize crew gets a hook in before you shake them loose. Repelling boarders is knife work, and knives don't care who was right.",
          damage: { hull: -30, weapons: -20 }, crewDeath: true }
      ]
    },
    {
      label: "Go dark in the junk field",
      cost: { power: -10 },
      outcomes: [
        { chance: 0.6, text: "You drift among the slag and count heartbeats for two days while their searchlights sweep past.",
          delayDays: 2 },
        { chance: 0.4, text: "Their spotter is good. You take fire coming out the far side — but you come out.",
          damage: { hull: -18, engines: -12 } }
      ]
    },
    {
      label: "Play dead, then burn",
      cost: { fuel: -15 },
      outcomes: [
        { chance: 0.5, text: "Their tender is halfway across when you light the drives. The channel fills with swearing. It's almost worth the fuel.",
          repair: { morale: 8 } },
        { chance: 0.5, text: "They board before you fire. You burn anyway, half a prize crew still aboard, and evict them the hard way at full thrust. They loot on the way out.",
          effects: { food: -15, parts: -3 }, damage: { hull: -10, morale: -8 } }
      ]
    }
  ]
},
{
  id: "pir-007",
  title: "The Long-Out Ship",
  category: "pirates",
  text: "Corsair lines, but wrong: running lights arranged like a face, the hull scrimshawed with names — hundreds of them. The hail is one voice made of several, slightly out of sync. 'You're new. We were new once. Trade us something warm and we'll let the dark keep you.'",
  zones: ["deep"],
  choices: [
    {
      label: "Trade them something warm",
      cost: { food: -8 },
      outcomes: [
        { chance: 0.6, text: "You send soup and old voice logs across. They send charts of the deep in return — hand-drawn, precise, and disturbing. Your navigator uses them anyway.",
          repair: { astrogation: 15 }, damage: { morale: -5 } },
        { chance: 0.4, text: "They linger a day, station-keeping close, singing on the hull frequency. They leave a gift welded to your airlock. Nobody remembers hearing them attach it.",
          effects: { parts: 2 }, damage: { morale: -12 }, delayDays: 1 }
      ]
    },
    {
      label: "Send someone across to talk",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.55, text: "Your volunteer comes back with salvage routes and a flask of something they refuse to discuss. They don't sleep facing the viewport anymore.",
          effects: { fuel: 10, parts: 3 }, damage: { morale: -8 } },
        { chance: 0.3, text: "They come back polite, calm, and wrong. The doc pumps them full of sedatives and hopes it's temporary.",
          effects: { meds: -2 }, damage: { morale: -15 } },
        { chance: 0.15, text: "The long-out ship breaks orbit an hour later. Your crewmate waves from their viewport. You think it's them.",
          effects: { crew: -1 }, damage: { morale: -20 } }
      ]
    },
    {
      label: "Go dark and pull away",
      cost: { fuel: -10, power: -5 },
      outcomes: [
        { chance: 0.7, text: "They don't chase. They just sing your ship's name — the old one, the registry you painted over — until you're out of range.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "They follow for a day at a polite distance, every light on. Then, satisfied by something, they turn away.",
          delayDays: 1, damage: { morale: -12 } }
      ]
    }
  ]
},
{
  id: "pir-008",
  title: "No Guns, No Vote",
  category: "pirates",
  text: "The cutter reads your dead point-defense grid from three thousand klicks and doesn't bother posturing — it comes straight in with a breaching torch. 'No guns, no vote,' someone laughs on the open channel. Grapples thunk against your hull like knuckles on a door.",
  zones: ["belt", "outer"],
  requires: { systemDamaged: ["weapons"] },
  choices: [
    {
      label: "Repel boarders at the lock",
      detail: "Cutting bars, fire extinguishers, bad intentions",
      outcomes: [
        { chance: 0.5, text: "Boarding armor beats jumpsuits, but the corridor is yours and they have to come through it single file. You hold the lock. The medbay earns its keep tonight.",
          effects: { meds: -3 }, damage: { hull: -10 }, repair: { morale: 10 } },
        { chance: 0.3, text: "You hold, barely. The corridor needs new paint and half your crew needs stitches.",
          effects: { meds: -5 }, damage: { hull: -12, morale: -5 } },
        { chance: 0.2, text: "You hold the lock. Afterward you carry someone to the medbay. They don't leave it.",
          damage: { hull: -12 }, crewDeath: true }
      ]
    },
    {
      label: "Seal the core, let them strip the holds",
      outcomes: [
        { chance: 0.7, text: "You listen through the bulkhead while they shop. Efficient, unhurried, gone in an hour. Your holds echo now.",
          effects: { food: -20, parts: -5, fuel: -10 }, damage: { morale: -12 } },
        { chance: 0.3, text: "They're thorough. They find the sealed lockers and take their time, because who's going to stop them? Nobody. That was the plan.",
          effects: { food: -25, parts: -6, meds: -4, fuel: -12 }, damage: { morale: -15 } }
      ]
    },
    {
      label: "Vent the boarding corridor",
      detail: "The ship is a weapon, if you're willing",
      cost: { oxygen: -10 },
      outcomes: [
        { chance: 0.65, text: "The grapples release. The cutter runs. You recover their gear from the corridor afterward. Nobody volunteers for that detail twice.",
          effects: { parts: 2 }, damage: { morale: -10 } },
        { chance: 0.35, text: "They blow the inner door before you finish venting. It gets ugly on both sides of the bulkhead — but they leave.",
          effects: { meds: -3 }, damage: { hull: -20 } }
      ]
    }
  ]
},
{
  id: "pir-009",
  title: "Corsair Colors",
  category: "pirates",
  text: "The gunship's silhouette is pure murder and its demand is standard: food, water, no heroes. But the weapon housings are empty. Painted barrels. Under the corsair skin it's a refugee barge, packed with heat signatures, half of them small. Their bluff. Your call.",
  zones: ["inner", "belt"],
  choices: [
    {
      label: "Call the bluff",
      outcomes: [
        { chance: 0.75, text: "The corsair voice cracks and becomes a woman asking you to wait. You don't. The channel stays open longer than you'd like.",
          damage: { morale: -10 } },
        { chance: 0.25, text: "A dying barge has exactly one weapon left. They ram. There was nothing aboard worth this — which they knew.",
          damage: { hull: -15, morale: -15 } }
      ]
    },
    {
      label: "Feed them anyway",
      detail: "The bluff worked. Just not the way they planned.",
      cost: { food: -12, water: -8 },
      outcomes: [
        { chance: 0.7, text: "The corsair paint hides a hundred people who now get another month. They hand over their real chart of local patrols. Everyone waves. It's absurd.",
          repair: { morale: 10 } },
        { chance: 0.3, text: "It isn't enough. It was never going to be enough. You pull away from the begging on the channel and eat dinner in silence.",
          damage: { morale: -8 } }
      ]
    },
    {
      label: "Board and verify",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.6, text: "The dead corsair's ship yields real salvage the refugees couldn't use. You trade some for passage-lore and leave feeling like a scavenger with manners.",
          effects: { parts: 3, meds: 2 }, damage: { morale: -8 } },
        { chance: 0.4, text: "Inside is what desperation actually looks like. Your medic won't leave until she's used half her kit, and you don't order her to.",
          effects: { meds: -4 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "pir-010",
  title: "The Beacon That Lies",
  category: "pirates",
  text: "A lifepod beacon sobs on the guard channel: coordinates, a failing voice, a child's name. On the third loop it's word-for-word identical. Somebody wrote that script, and somebody hangs nets around it. Out here, even mercy has been weaponized.",
  zones: ["belt", "outer"],
  sensorWarning: "Distress beacon ahead. The signal repeats — identically.",
  choices: [
    {
      label: "Answer it anyway",
      outcomes: [
        { chance: 0.55, text: "The pod is bait and the net is fast. You fight free of the grapple drones with your hull screaming and your rescue instinct lighter by one.",
          effects: { fuel: -10 }, damage: { hull: -20, morale: -8 } },
        { chance: 0.25, text: "The ambush springs. You take the hits, grab the pod anyway out of spite, and find it packed with the last crew's salvage.",
          effects: { parts: 4, fuel: -10 }, damage: { hull: -15 } },
        { chance: 0.2, text: "The net is old, its owners dead or moved on. The pod holds a mannequin and a transmitter. Someone built this. Nobody talks at dinner.",
          effects: { parts: 3 }, damage: { morale: -10 } }
      ]
    },
    {
      label: "Log it and pass",
      outcomes: [
        { chance: 0.8, text: "Probably a trap. Almost certainly. The word 'almost' rides with you for days.",
          damage: { morale: -8 } },
        { chance: 0.2, text: "The lure's owners chase fleeing witnesses on principle. You outrun the half-hearted pursuit, mostly.",
          effects: { fuel: -8 }, damage: { morale: -8, hull: -8 } }
      ]
    },
    {
      label: "Send a decoy drone",
      detail: "Built from scrap and spare hopes",
      requires: { resources: { parts: 3 } },
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.7, text: "The net springs on your drone. You map every gun position as they fire and slide past clean, feeling briefly clever.",
          repair: { morale: 8 } },
        { chance: 0.3, text: "The drone dies without springing anything. Then the net springs on you. You burn hard and lose them in the dark.",
          effects: { fuel: -10 }, damage: { engines: -8 } }
      ]
    }
  ]
},
{
  id: "pir-011",
  title: "Someone Else's Bad Day",
  category: "pirates",
  text: "A fat freighter tears past with a corsair cutter on its tail, both channels screaming. Not your fight. Not your cargo. Not your problem — unless you make it yours, or wait for the shooting to stop and see what's left over.",
  zones: ["belt"],
  choices: [
    {
      label: "Intervene",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.5, text: "The corsair wants easy prey, not a fair fight. It breaks off. The freighter's captain pays in cargo and means every crate.",
          effects: { food: 15, fuel: 8 }, damage: { weapons: -8, hull: -10 } },
        { chance: 0.3, text: "The corsair decides you're the better prize. The freighter escapes while you're busy being shot at. You're not even angry. Much.",
          damage: { hull: -20, weapons: -12 } },
        { chance: 0.2, text: "You arrive in time to salvage the freighter's wreckage. The corsair leaves you to it, laughing on the guard channel.",
          effects: { parts: 3 }, damage: { morale: -12 } }
      ]
    },
    {
      label: "Hold your course",
      outcomes: [
        { chance: 0.7, text: "The drives flare and dwindle. You'll never know how it ended. The crew doesn't ask your reasons, which is worse than if they had.",
          damage: { morale: -6 } },
        { chance: 0.3, text: "The corsair puts one round into you in passing — a witness tax. Out here, even minding your own business costs.",
          damage: { hull: -10, morale: -6 } }
      ]
    },
    {
      label: "Wait, then pick over the aftermath",
      outcomes: [
        { chance: 0.5, text: "The freighter got away after all. You loiter over cooling engine wash like a vulture at an empty table.",
          delayDays: 1, damage: { morale: -4 } },
        { chance: 0.5, text: "There's plenty left. The corsair took the valuables and left the groceries. You don't look at the escape pods that didn't launch.",
          delayDays: 1, effects: { food: 10, parts: 3 }, damage: { morale: -10 } }
      ]
    }
  ]
},
{
  id: "pir-012",
  title: "Interest on Kindness",
  category: "pirates",
  text: "A familiar welded skiff matches your course — the kids you fed. 'Corridor ahead is netted. We watched them rig it. We'll show you the gap for a ride's worth of water.' Charity coming back around, or bait wearing a face you trust.",
  zones: ["inner", "belt"],
  requires: { requiresFlag: "fed_skiffs" },
  choices: [
    {
      label: "Pay the water, take the gap",
      cost: { water: -8 },
      outcomes: [
        { chance: 0.75, text: "The gap is real, threaded between sensor buoys. The kids wave you through and point out a salvage cache on the way — interest on your kindness.",
          effects: { parts: 2 }, repair: { morale: 8 } },
        { chance: 0.25, text: "The gap closed yesterday. The kids scream warnings as the net guns wake. You get through scorched but whole, and they cover you the whole way. They tried.",
          effects: { fuel: -8 }, damage: { hull: -12 } }
      ]
    },
    {
      label: "Decline politely, go around",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 1.0, text: "You burn the long way around the marked corridor. Slow, expensive, alive. The skiff shadows you for an hour, then peels away, hurt.",
          delayDays: 1, damage: { morale: -5 } }
      ]
    },
    {
      label: "Nets are a rumor. Straight through.",
      outcomes: [
        { chance: 0.35, text: "You thread the corridor at full evasive burn and nothing shoots. The crew is split on whether the kids lied or you got lucky. Bets are placed.",
          damage: { engines: -8 } },
        { chance: 0.65, text: "The net is exactly where they said. The corsairs take a boarding tax at gunpoint and let you limp on. The kids watched. That's the part that stings.",
          effects: { fuel: -10, food: -12 }, damage: { hull: -22, morale: -10 } }
      ]
    }
  ]
},
{
  id: "pir-013",
  title: "The Dead Man's Toll",
  category: "pirates",
  text: "The toll platform is older than most governments. Its clan is twenty years dead, but the guns still track and the ledger still bills: 'DEPOSIT TRIBUTE.' The receptacle is stuffed with two decades of tribute nobody ever collected. The dead run the tightest operations.",
  zones: ["deep"],
  choices: [
    {
      label: "Pay the ghost its toll",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.8, text: "The guns stand down with a courteous chime. Gliding past, you see the hoard through the platform's viewport — twenty years of wealth, welded in with a corpse in a captain's chair.",
          damage: { morale: -8 } },
        { chance: 0.2, text: "Your tribute registers. The ancient IFF doesn't. The platform bills you and shoots you, which even the crew admits is very corporate.",
          damage: { hull: -15 } }
      ]
    },
    {
      label: "Crack the tribute vault",
      detail: "The dead don't file complaints",
      outcomes: [
        { chance: 0.45, text: "The vault opens. So do the gun ports. You leave rich and ventilated, chased by a dead clan's outrage.",
          effects: { parts: 5, meds: 3, food: 10, fuel: -8 }, damage: { hull: -18 } },
        { chance: 0.35, text: "You grab an armful before the grid wakes. Not the hoard. Enough.",
          effects: { parts: 2 }, damage: { hull: -12 } },
        { chance: 0.2, text: "The defense grid was saving itself for exactly this. You escape with nothing but structural damage and a new respect for the dead.",
          damage: { hull: -28, engines: -10, morale: -10 } }
      ]
    },
    {
      label: "Wide berth",
      cost: { fuel: -12 },
      outcomes: [
        { chance: 1.0, text: "You burn around the platform's gun envelope. The crew argues about the money for three days. The dead man keeps it, as he has kept it from everyone.",
          delayDays: 1, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "pir-014",
  title: "Recruiters",
  category: "pirates",
  text: "The corsair doesn't shoot. It broadcasts: hot meals, real bunks, shares for every hand, footage of a mess hall with actual bread. 'Your captain is hauling you somewhere to die. Our airlock's open.' Your crew heard all of it. They're very quiet.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Jam it and burn past",
      cost: { power: -8, fuel: -8 },
      outcomes: [
        { chance: 0.7, text: "Static answers the sales pitch. But jamming isn't an argument, and everyone knows what the silence said.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "The jammer chokes halfway through the bread footage. Bread. Your galley hasn't seen flour in months.",
          damage: { morale: -15 } }
      ]
    },
    {
      label: "Debate them on the open channel",
      outcomes: [
        { chance: 0.55, text: "You don't promise bread. You promise a destination and mean it, and your crew hears the difference. The corsair, delighted by the show, invoices a 'performance fee.' You pay it. Worth it.",
          effects: { food: -8 }, repair: { morale: 12 } },
        { chance: 0.45, text: "The pirate is funnier than you. You spend the next day talking your dock hand out of the airlock queue and hating a stranger's timing.",
          delayDays: 1, damage: { morale: -18 } }
      ]
    },
    {
      label: "Open the airlock. Anyone can go.",
      requires: { minCrew: 3 },
      outcomes: [
        { chance: 0.6, text: "Nobody goes. Staying, when the door is open, means something staying never meant before. The corsair leaves a parting shot in your flank out of professional annoyance.",
          damage: { hull: -8 }, repair: { morale: 15 } },
        { chance: 0.4, text: "One crewmate takes the offer. You watch the shuttle cross and think about bread. The ones who stay, stay harder now.",
          effects: { crew: -1 }, damage: { morale: -10 } }
      ]
    }
  ]
}
);
