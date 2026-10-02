/* ORION TRAIL — rare & absurd. Prefix: wild- . Keep weight ~0.3. */
SCENARIOS.push(
{
  id: "wild-001",
  title: "The Manager",
  category: "wildcards",
  weight: 0.3,
  text: "An ancient customer-service probe locks onto your hull and begins broadcasting on all channels: 'YOUR CALL IS IMPORTANT TO US. PLEASE PROVIDE YOUR ACCOUNT NUMBER.' It has been holding since before the Collapse. It would like to speak to your manager.",
  choices: [
    {
      label: "Put the captain on the line",
      outcomes: [
        { chance: 0.7, text: "Forty minutes of hold music later, the probe declares your complaint RESOLVED, dispenses a coupon, and releases a courtesy supply pod nobody claimed in sixty years.",
          effects: { food: 8, parts: 2 }, repair: { morale: 8 } },
        { chance: 0.3, text: "The probe escalates you to a supervisor probe. The supervisor probe does not exist. The hold music is now inside the ship's PA and cannot be removed by conventional means.",
          damage: { morale: -6, sensors: -5 } }
      ]
    },
    {
      label: "Pry it off the hull",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.8, text: "It detaches with a wounded beep and drifts away, still holding. Somewhere, a ticket remains open forever.",
          effects: {} },
        { chance: 0.2, text: "It clamps down harder and applies a LATE FEE, which it deducts directly from your power grid.",
          effects: { power: -12 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "wild-002",
  title: "Leviathan",
  category: "wildcards",
  weight: 0.3,
  text: "Something surfaces from the dark — whale-shaped, moon-adjacent, singing in gravitational waves. It has no business existing and no interest in your opinion on the matter. It has noticed your ship. It appears to think you are a calf. It is coming over to nuzzle.",
  choices: [
    {
      label: "Hold still and let it happen",
      detail: "It seems friendly. It is also the size of a moon.",
      outcomes: [
        { chance: 0.6, text: "It nuzzles the hull with the tenderness of a continent. The crew watches through the viewport in reverent silence, then spends a week describing it to each other, badly.",
          repair: { morale: 10 }, damage: { hull: -10 } },
        { chance: 0.4, text: "It sneezes. You are two days from where you were, facing the wrong way, coated in something science will need a new word for.",
          damage: { hull: -15 }, delayDays: 2 }
      ]
    },
    {
      label: "Burn hard and run",
      detail: "It is faster than you. Hopefully it is also lazier.",
      cost: { fuel: -10 },
      outcomes: [
        { chance: 0.7, text: "You escape. Behind you it sings one long note that your sensors log, unprompted, as 'disappointment.' Nobody talks for a while.",
          damage: { morale: -5 } },
        { chance: 0.3, text: "Its wake catches you mid-burn and tumbles the ship like laundry. The engines file their objection formally.",
          damage: { engines: -15 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "wild-003",
  title: "Local 7 (Hydroponics)",
  category: "wildcards",
  weight: 0.3,
  text: "The mold in hydroponics has achieved sentience, formed a union, and gone on strike. Its demands, spelled in spores across the lettuce: shorter light cycles, higher humidity, and an apology. Food production is suspended pending negotiations. The mold has also, somehow, obtained a tiny sign.",
  choices: [
    {
      label: "Negotiate in good faith",
      detail: "The humidity concession will cost water.",
      cost: { water: -8 },
      outcomes: [
        { chance: 0.7, text: "Terms are reached. The apology is read over the PA. The mold returns to work and yields improve — it turns out it had opinions about crop rotation, all of them correct.",
          effects: { food: 12 }, repair: { morale: 5 } },
        { chance: 0.3, text: "Talks collapse over the apology's wording. The mold files a grievance with the air scrubbers, which come out in sympathy.",
          damage: { lifeSupport: -10, morale: -4 } }
      ]
    },
    {
      label: "Scrub the bay",
      detail: "Union-busting, with solvent.",
      outcomes: [
        { chance: 0.6, text: "The bay comes back online under new, non-sentient management. Yields recover. The crew crossed a picket line and knows it.",
          effects: { food: 8 }, damage: { morale: -8 } },
        { chance: 0.4, text: "The mold anticipated this and had already organized the water reclaimer. The strike is general now.",
          effects: { water: -15 }, damage: { morale: -8 } }
      ]
    }
  ]
},
{
  id: "wild-004",
  title: "Happy Birthday, Craig",
  category: "wildcards",
  weight: 0.3,
  text: "A catering drone matches your velocity and unfurls a banner: HAPPY 40TH, CRAIG. Nobody aboard is named Craig. Nobody is turning forty. The drone insists this is the correct ship, cites a contract, and begins staging hors d'oeuvres against the airlock. It has been paid in advance.",
  choices: [
    {
      label: "Someone becomes Craig",
      outcomes: [
        { chance: 0.75, text: "The party is catered, competent, and strangely moving. The drone screens a slideshow of a stranger's life, and the crew weeps openly at photographs of a man none of you have ever met.",
          effects: { food: 10 }, repair: { morale: 10 } },
        { chance: 0.25, text: "The drone asks the security question only Craig would know. You guess wrong. It repossesses the canapés with prejudice and paints a one-star review across your hull on the way out.",
          damage: { morale: -6, hull: -5 } }
      ]
    },
    {
      label: "Refuse delivery",
      outcomes: [
        { chance: 0.7, text: "The drone leaves, banner drooping. The crew is professional about it, in the way of people who will bring up the cake, individually, for months.",
          damage: { morale: -6 } },
        { chance: 0.3, text: "Per contract, non-acceptance triggers doorstep delivery. It fires the entire order through the airlock, candles lit. There is briefly cake, and then there is briefly fire.",
          effects: { food: 8 }, damage: { lifeSupport: -10 } }
      ]
    }
  ]
},
{
  id: "wild-005",
  title: "The Sole Beneficiary",
  category: "wildcards",
  weight: 0.3,
  text: "You recover a drifting escape pod. Inside: one potted fern, healthy and recently watered, strapped into the crash seat, and a notarized will naming the fern sole heir to a mining fortune on a moon that no longer exists. No body. The pod's log reads, in full: 'He knows what he did.'",
  choices: [
    {
      label: "Adopt the fern",
      detail: "It is rich, and it needs watering.",
      cost: { water: -5 },
      outcomes: [
        { chance: 0.8, text: "The fern takes the conn — decoratively. It radiates oxygen, judgment, and quiet authority. Within a week the crew salutes it without irony, and productivity is, insultingly, up.",
          effects: { oxygen: 5 }, repair: { morale: 8 } },
        { chance: 0.2, text: "The fern's soil turns out to hold strong opinions about your air mixture, and the spores to enforce them.",
          damage: { lifeSupport: -10, morale: -4 } }
      ]
    },
    {
      label: "Strip the pod for salvage",
      outcomes: [
        { chance: 0.7, text: "Salvage is salvage. The fern goes out the airlock still upright, dignified to the last. Nobody talks about it. Everybody thinks about it.",
          effects: { parts: 4 }, damage: { morale: -6 } },
        { chance: 0.3, text: "Mid-salvage, the estate's executor drone arrives and fines you for interference with probate. You are now, legally, in debt to a houseplant.",
          effects: { power: -10 }, damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "wild-006",
  title: "K-EARTH, All The Hits",
  category: "wildcards",
  weight: 0.3,
  text: "A rock on your route is broadcasting pirate radio: every great song Earth ever recorded, spun by a DJ AI who does not know Earth is gone. Between tracks it reads traffic reports for cities that no longer exist. The signal is charming, nostalgic, and stomping all over your navigation band.",
  choices: [
    {
      label: "Let it play",
      outcomes: [
        { chance: 0.7, text: "Three glorious days of golden oldies. Morale soars. So does your drift off the plotted line, because every nav advisory arrived buried under a doo-wop marathon.",
          repair: { morale: 12 }, damage: { astrogation: -10 } },
        { chance: 0.3, text: "The DJ takes a caller request. The call originates from your ship. Nobody confesses. The song is 'Don't Stop Believin'.' It plays fourteen consecutive times.",
          damage: { morale: -6 }, delayDays: 1 }
      ]
    },
    {
      label: "Jam the frequency",
      detail: "Silence costs power. Also, the DJ has feelings.",
      cost: { power: -8 },
      outcomes: [
        { chance: 0.8, text: "Static, then silence, then the clean chatter of the nav band. The crew hums the last song for weeks, in a way that is somehow worse.",
          effects: {} },
        { chance: 0.2, text: "The DJ notices, dedicates the next block 'to the haters in the fast lane,' and boosts transmission. Your comms array files for hazard pay.",
          damage: { sensors: -10, morale: -5 } }
      ]
    }
  ]
},
{
  id: "wild-007",
  title: "Estate v. Estate",
  category: "wildcards",
  weight: 0.3,
  text: "Two derelict freighters hang nose to nose, their AIs locked in an automated collision lawsuit since before the Collapse. Both crews are decades gone. Your transponder pings — you have been served. You are now a witness in Estate v. Estate. The court thanks you for your mandatory cooperation.",
  choices: [
    {
      label: "Testify",
      outcomes: [
        { chance: 0.6, text: "You give testimony on a crash you never saw to a judge that does not exist. Both parties are found liable, both estates dissolved, and court costs awarded to you — payable in salvage.",
          effects: { parts: 5, fuel: 8 }, delayDays: 1 },
        { chance: 0.4, text: "Cross-examination lasts nine hours. You are found in contempt for yawning and fined. The bailiff drone is very sorry, but rules are rules.",
          effects: { power: -10 }, damage: { morale: -6 }, delayDays: 2 }
      ]
    },
    {
      label: "Flee the jurisdiction",
      detail: "A hard burn out of subpoena range.",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.7, text: "You escape. A warrant for your arrest is issued and forwarded to a government that stopped existing sixty years ago. It will be processed accordingly.",
          effects: {} },
        { chance: 0.3, text: "For the first time in decades, the two derelicts agree on something: contempt of court. Their point-defense turrets, unlike their crews, still work.",
          damage: { hull: -15 } }
      ]
    }
  ]
},
{
  id: "wild-008",
  title: "One Weird Trick",
  category: "wildcards",
  weight: 0.3,
  text: "A glitching advertising buoy hails you with an offer: fuel credits, real and transferable, in exchange for watching one sponsored message. The sponsor has been dead for sixty years. The message is 'up to' eleven minutes long. There is a skip button. The skip button is decorative.",
  choices: [
    {
      label: "Watch the ad",
      outcomes: [
        { chance: 0.6, text: "Eleven minutes of a smiling man selling beachfront property on Earth. The buoy pays out promptly from a dead company's account. Nobody meets anybody's eyes afterward.",
          effects: { fuel: 10 }, damage: { morale: -4 } },
        { chance: 0.4, text: "It is pre-roll all the way down — ads before ads before ads. The buoy pays double for your 'engagement.' Something in the crew has gone quiet and will not be back today.",
          effects: { fuel: 15 }, damage: { morale: -8 }, delayDays: 1 }
      ]
    },
    {
      label: "Decline politely",
      outcomes: [
        { chance: 0.8, text: "The buoy accepts your choice, respects your privacy, and marks you 'interested — retarget later.' It will find you. They always find you.",
          effects: {} },
        { chance: 0.2, text: "It interprets silence as consent and projects the ad directly onto your viewport at navigation-hazard brightness. Free trial, it insists. FREE TRIAL.",
          damage: { sensors: -10, morale: -4 } }
      ]
    }
  ]
},
{
  id: "wild-009",
  title: "The Adjuster",
  category: "wildcards",
  weight: 0.3,
  text: "A pristine shuttle docks without asking permission. Out steps the galaxy's last insurance adjuster — pressed suit, briefcase, clipboard — mid-audit since the Collapse. Your shipping line held a policy. The line is gone. The policy, he says, smiling, is eternal. He would like to take a look around.",
  choices: [
    {
      label: "Cooperate with the audit",
      outcomes: [
        { chance: 0.65, text: "A full day of inspections. He finds seventeen violations, waives sixteen, and approves a claim your ship filed before you were born. The payout pod arrives from nowhere. He tips his hat to the void.",
          effects: { parts: 5, meds: 3 }, delayDays: 1 },
        { chance: 0.35, text: "He finds the eighteenth violation. Your premium adjusts, retroactively, with interest. He drains the difference from your tanks as 'collateral' and leaves a complimentary pen.",
          effects: { fuel: -12, water: -8 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Deny everything",
      outcomes: [
        { chance: 0.6, text: "He nods, writes 'uncooperative' in beautiful cursive, and departs. You feel victorious until you find the DENIED sticker fused to the airlock's outer seal. It does not come off.",
          damage: { hull: -10, morale: -4 } },
        { chance: 0.4, text: "He studies you a long moment, then shrugs. 'Off the record, the policy was void anyway.' He leaves you his shuttle's emergency kit and a calendar for a year that already happened.",
          effects: { meds: 2, parts: 2 } }
      ]
    }
  ]
},
{
  id: "wild-010",
  title: "Exact Change Only",
  category: "wildcards",
  weight: 0.3,
  text: "A vending machine drifts past on a stable orbit — lights on, fully stocked, humming a compressor tune. It accepts only Earth currency, which has not existed for sixty years. Behind the glass: snacks, water bottles, and one first-aid kit in row F, slightly crooked. The crew presses against the viewport.",
  choices: [
    {
      label: "Crack it open",
      detail: "It belongs to no one. Probably.",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.7, text: "The lock yields. The machine sighs like a disappointed grandparent and surrenders its inventory. The chips are stale in a way that transcends time. The crew eats them anyway, reverently.",
          effects: { food: 10, water: 6, meds: 2 } },
        { chance: 0.3, text: "The anti-theft system is a taser. It was built in an era that took vending seriously. The charge arcs up the salvage arm into your grid, and the machine keeps humming.",
          effects: { power: -10 }, damage: { morale: -5 } }
      ]
    },
    {
      label: "Leave it be",
      outcomes: [
        { chance: 0.75, text: "You leave it turning slowly in the dark, a lighthouse for no one. The crew discusses row F, specifically, for eleven days.",
          damage: { morale: -5 } },
        { chance: 0.25, text: "As you pull away, it dispenses one item, free, into the void — a parting gesture. The salvage arm catches it. Row F. The first-aid kit, slightly crooked.",
          effects: { meds: 3 }, repair: { morale: 6 } }
      ]
    }
  ]
}
);
