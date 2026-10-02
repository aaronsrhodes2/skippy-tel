/* ORION TRAIL — caches & opportunities. Prefix: cache- */
SCENARIOS.push(
{
  id: "cache-001",
  title: "Comet of Opportunity",
  category: "caches",
  zones: ["belt", "outer", "deep"],
  text: "A long-period comet crosses your track, dirty ice by the megaton. Free water and oxygen for anyone willing to land a team on a snowball doing forty kilometers a second.",
  choices: [
    {
      label: "Full harvest operation",
      detail: "Two days, big haul, moving target",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.7, text: "The team carves ice like lumberjacks in slow motion. Tanks brim. The galley serves 'comet slush' and calls it morale food.",
          effects: { water: 30, oxygen: 20 }, repair: { morale: 4 }, delayDays: 2 },
        { chance: 0.3, text: "A gas pocket vents mid-harvest and scatters the team. Everyone comes back; not everything they carried does.",
          effects: { water: 15, oxygen: 8, parts: -2 }, damage: { morale: -5 }, delayDays: 2 }
      ]
    },
    {
      label: "Quick skim of the coma",
      cost: { fuel: -3 },
      outcomes: [
        { chance: 0.8, text: "You fly the plume with scoops open. A modest sip, no drama. The comet continues its business, unbothered since the dinosaurs.",
          effects: { water: 10, oxygen: 6 } },
        { chance: 0.2, text: "The plume is mostly dust with delusions of ice. The scoops collect grit and disappointment.",
          effects: { water: 3 }, damage: { sensors: -5 } }
      ]
    },
    {
      label: "Let it pass",
      outcomes: [
        { chance: 1, text: "It will come back this way in four thousand years. Optimistically, someone will be here to see it.",
          effects: {} }
      ]
    }
  ]
},
{
  id: "cache-002",
  title: "The Claim-Lock",
  category: "caches",
  text: "A supply depot hangs dark at a dead Lagrange point, ration crates visible through the docking port glass. The claim-lock still glows, faithfully guarding inventory for a corporation that stopped existing decades ago. Nobody has told it. Locks are terrible listeners.",
  choices: [
    {
      label: "Hack the claim-lock",
      detail: "Burn power feeding its handshake protocol",
      cost: { power: -10 },
      outcomes: [
        { chance: 0.65, text: "The lock accepts a forged executive override and opens with a chime of corporate gratitude. The shelves are real and mostly full.",
          effects: { food: 15, parts: 6, meds: 3 }, delayDays: 1 },
        { chance: 0.35, text: "The lock's counter-intrusion suite fights back and slags half your sensor bus before you yank the cable. You grab what fell off a pallet.",
          effects: { food: 5 }, damage: { sensors: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Cut through the bulkhead",
      detail: "Skip the argument, spend the hardware",
      cost: { parts: -4 },
      outcomes: [
        { chance: 0.7, text: "The torch wins. Inside: crates, cold and orderly, addressed to customers who are now geology.",
          effects: { food: 18, water: 8 }, delayDays: 1 },
        { chance: 0.3, text: "Cutting the wall trips the anti-theft scuttle charge. Most of the pantry becomes shrapnel, and some of the shrapnel meets your hull.",
          effects: { food: 4 }, damage: { hull: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "Leave it locked",
      outcomes: [
        { chance: 1, text: "The crew watches a full pantry recede in the aft camera. Dinner that night is beige and quietly resentful.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "cache-003",
  title: "Geyser Season",
  category: "caches",
  zones: ["outer"],
  text: "An unnamed ice moon is venting — geysers of water and volatiles punching hundreds of kilometers into vacuum, right along your track. Free tankage, if you don't mind flying through a car wash operated by a moon.",
  choices: [
    {
      label: "Deep plume run",
      detail: "Fly the throat of the geyser, scoops wide",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.6, text: "You thread the plume like a needle full of ice. Tanks slam to the stops. The pilot will be insufferable for a week.",
          effects: { water: 25, oxygen: 12 }, delayDays: 1 },
        { chance: 0.4, text: "The plume carries rocks the survey chart didn't mention. The hull learns this empirically. Still a decent haul.",
          effects: { water: 10, oxygen: 4 }, damage: { hull: -10 }, delayDays: 1 }
      ]
    },
    {
      label: "High skim",
      detail: "Thin pickings at a safe altitude",
      cost: { fuel: -4 },
      outcomes: [
        { chance: 0.75, text: "You sip the plume's ragged crown. A modest, dignified haul, acquired without a single alarm.",
          effects: { water: 12, oxygen: 5 } },
        { chance: 0.25, text: "The geyser sputters out the moment you arrive, as geysers do. You collect mist and the crew's commentary.",
          effects: { water: 4 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Stay on course",
      outcomes: [
        { chance: 1, text: "The moon keeps hurling water at the stars, and you keep rationing it by the cup. Nobody says anything. Loudly.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-004",
  title: "Navy Drop-Tanks",
  category: "caches",
  zones: ["belt", "outer"],
  text: "A pre-Collapse fuel dump: twelve navy drop-tanks strung on a kilometer of tether, waiting for a fleet that never came back. Half read empty. The rest are sublimating gently into the dark, like an inheritance being spent by nobody.",
  choices: [
    {
      label: "Drain the whole string",
      detail: "Two days of pumping, power for the transfer rig",
      cost: { power: -6 },
      outcomes: [
        { chance: 0.55, text: "The pumps run hot for two days and the ledger runs beautiful. Old navy fuel burns fine. Better than yours, honestly.",
          effects: { fuel: 25 }, delayDays: 2 },
        { chance: 0.35, text: "Most tanks are emptier than their gauges admit — decades of slow boil-off. You collect the dregs and a lesson about trusting dead men's paperwork.",
          effects: { fuel: 12 }, damage: { morale: -3 }, delayDays: 2 },
        { chance: 0.1, text: "Tank nine ruptures mid-transfer and vents flaming propellant across your intake manifold. You keep what was already aboard.",
          effects: { fuel: 8 }, damage: { engines: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Tap the two best tanks",
      detail: "Quick siphon, minimal ceremony",
      cost: { power: -3 },
      outcomes: [
        { chance: 0.8, text: "In, siphon, out. The fullest tanks give up their fuel without complaint. You leave the rest for the next optimist.",
          effects: { fuel: 10 } },
        { chance: 0.2, text: "The transfer valves crumble like biscuits — forty years of vacuum welding. You lose fittings prying them loose for a mouthful of fuel.",
          effects: { fuel: 3, parts: -2 } }
      ]
    },
    {
      label: "Leave it",
      outcomes: [
        { chance: 1, text: "Free fuel receding behind you is a special kind of headache. The navigator recalculates the reserve margin twice, then stops sharing the number.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-005",
  title: "The Collector Farm",
  category: "caches",
  zones: ["inner"],
  text: "A solar collector farm the size of a county still tracks the sun with mindless devotion, charging storage banks for customers who have been dead thirty years. The batteries are full. They have been full for a very long time.",
  choices: [
    {
      label: "Dock and charge everything",
      detail: "Two days on the grid, fuel to match orbits",
      cost: { fuel: -3 },
      outcomes: [
        { chance: 0.7, text: "Your banks drink for two days straight. Lights at full, heaters at full, someone runs the water hot just because they can.",
          effects: { power: 30 }, repair: { morale: 3 }, delayDays: 2 },
        { chance: 0.3, text: "The farm's grid regulator died sometime last decade. A surge rams your generator hard enough to smell. You still net a good charge.",
          effects: { power: 15 }, damage: { generator: -10 }, delayDays: 2 }
      ]
    },
    {
      label: "Pull their storage cells",
      detail: "Rip out hardware, no docking, no waiting",
      cost: { parts: -3 },
      outcomes: [
        { chance: 0.75, text: "The EVA team unbolts a rack of charged cells and ferries them home like heavy, ill-tempered groceries.",
          effects: { power: 18 } },
        { chance: 0.25, text: "The cells are swollen and half-dead under their status lights. You get a trickle for your trouble and some very heavy paperweights.",
          effects: { power: 6 }, damage: { morale: -3 } }
      ]
    },
    {
      label: "Keep flying",
      outcomes: [
        { chance: 1, text: "The farm turns its million mirrors after the sun as you pass, generous and pointless, like a lighthouse for a drowned coast.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-006",
  title: "Feral Greenhouse",
  category: "caches",
  text: "An automated hydroponics barge, adrift and running feral for a generation. Tomato vines press against the viewports from the inside. The manifest promises produce. The viewports promise a jungle with strong opinions about visitors.",
  choices: [
    {
      label: "Harvest hold by hold",
      detail: "Full boarding op — suits burn air, jungle fights back",
      cost: { oxygen: -4 },
      outcomes: [
        { chance: 0.65, text: "Two days of machete botany. The crew comes back scratched, sneezing, and hauling actual fresh food. Someone cries over a strawberry. You log it as morale.",
          effects: { food: 25 }, repair: { morale: 4 }, delayDays: 2 },
        { chance: 0.35, text: "The barge's pollen puts half the boarding team into anaphylaxis. You retreat with a modest haul and a busy infirmary.",
          effects: { food: 10, meds: -3 }, damage: { morale: -4 }, delayDays: 2 }
      ]
    },
    {
      label: "Raid the forward hold only",
      detail: "Quick grab near the airlock",
      cost: { oxygen: -2 },
      outcomes: [
        { chance: 0.8, text: "You strip the nearest racks and leave before the deeper green notices. The tomatoes have opinions, but no reach.",
          effects: { food: 10 } },
        { chance: 0.2, text: "Spores ride home in the suit vents and colonize your air filters. Life support develops a faint smell of compost and betrayal.",
          effects: { food: 3 }, damage: { lifeSupport: -10 } }
      ]
    },
    {
      label: "Not the tomatoes",
      outcomes: [
        { chance: 1, text: "You leave the jungle to its slow conquest of the barge. The galley serves reconstituted paste under a sign that used to say VARIETY.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-007",
  title: "IFF Challenge",
  category: "caches",
  zones: ["belt", "outer"],
  sensorWarning: "Sensors flag regular geometry ahead — a grid of objects too neat to be rock, holding formation nobody ordered.",
  text: "A military prepositioning cache: armored pods in parade-ground formation around a sentry buoy. The buoy is transmitting an IFF challenge on a loop, to a war that ended before your ship was laid down. It still has ammunition. It mentions this.",
  choices: [
    {
      label: "Answer the challenge",
      detail: "Requires working weapons — your fire-control transponder can handshake",
      requires: { systemOk: ["weapons"] },
      outcomes: [
        { chance: 0.85, text: "Your transponder squawks a century-old friendly code and the buoy stands down, delighted to finally resupply somebody. The pods open like ration tins.",
          effects: { parts: 15, meds: 4 }, repair: { weapons: 10 } },
        { chance: 0.15, text: "The codes rotate mid-handshake — of course they rotate — and the buoy fires a disciplinary warning shot. You loot one pod at speed.",
          effects: { parts: 6 }, damage: { hull: -10 } }
      ]
    },
    {
      label: "Run the perimeter blind",
      detail: "Dodge the sentry arcs, gamble on the pods",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.5, text: "You slip between sentry arcs like a burglar in a museum and crack two pods. Military packaging: everything labeled, nothing expired.",
          effects: { parts: 15 }, delayDays: 1 },
        { chance: 0.3, text: "The buoy's targeting is senile but its guns are not. A sentry round rakes the hull before you clear its arc with one pod's worth of salvage.",
          effects: { parts: 5 }, damage: { hull: -15 } },
        { chance: 0.2, text: "The pods you reach are already cracked and stripped. Somebody ran this gauntlet decades ago and left you the risk without the reward.",
          effects: {}, damage: { morale: -3 }, delayDays: 1 }
      ]
    },
    {
      label: "Log it and leave",
      outcomes: [
        { chance: 1, text: "You mark the cache on the chart for some braver ship. The buoy keeps challenging the dark, patient as artillery.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-008",
  title: "Mercy Station",
  category: "caches",
  text: "A mothballed medical relay drifts in standby, its red cross faded to a diplomatic pink. Automated triage answers your hail immediately, warmly, and asks you to describe your symptoms. It has been waiting a long time for a patient.",
  choices: [
    {
      label: "Present as patients",
      detail: "Power its systems, sit through the triage protocol",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.7, text: "The station examines everyone, tuts electronically at your bloodwork, and dispenses supplies with printed aftercare instructions. The crew feels looked-after. It's unsettling.",
          effects: { meds: 8 }, repair: { morale: 3 }, delayDays: 1 },
        { chance: 0.3, text: "The dispensary drum turns and delivers mostly expiration dates. The triage AI apologizes at length, which somehow makes it worse.",
          effects: { meds: 2 }, damage: { morale: -3 }, delayDays: 1 }
      ]
    },
    {
      label: "Strip the pharmacy",
      detail: "Skip the questionnaire, force the vault",
      cost: { parts: -2 },
      outcomes: [
        { chance: 0.75, text: "You crack the cold-vault and take the good shelf: sealed meds and a crate of patient-recovery rations, shelf-stable unto the heat death.",
          effects: { meds: 6, food: 5 } },
        { chance: 0.25, text: "The vault purges on tamper — preservation protocol. Foam entombs the pharmacy while the triage AI reads you the relevant statute.",
          effects: { meds: 1 }, damage: { morale: -3 }, delayDays: 1 }
      ]
    },
    {
      label: "Leave it for someone sicker",
      outcomes: [
        { chance: 1, text: "You sign off. The station wishes you continued health with what sounds, at this range, like genuine disappointment.",
          damage: { morale: -2 } }
      ]
    }
  ]
},
{
  id: "cache-009",
  title: "The Keeper's Garden",
  category: "caches",
  zones: ["belt"],
  sensorWarning: "Sensors read a hollow return inside an unremarkable rock — warm, pressurized, and photosynthesizing.",
  text: "Inside a spun-up asteroid: a garden. Moss banks, dwarf fruit trees, a pond, air so oxygen-rich it makes you dizzy at the hatch. The keeper is present, seated by the water, mummified and peaceful. Whatever he built, it outlived him without noticing.",
  choices: [
    {
      label: "Full top-up and cuttings",
      detail: "Two days running compressors in a dead man's orchard",
      cost: { fuel: -4 },
      outcomes: [
        { chance: 0.7, text: "You bottle his air and pick his fruit, carefully, leaving the garden alive. Someone salutes the keeper on the way out. Nobody laughs.",
          effects: { oxygen: 25, food: 8 }, repair: { morale: 3 }, delayDays: 2 },
        { chance: 0.3, text: "The compressor lines chew up under the load, and the ecology sulks — leaves browning where the intakes ran. You take less than planned and feel like vandals.",
          effects: { oxygen: 12, parts: -2 }, damage: { morale: -4 }, delayDays: 2 }
      ]
    },
    {
      label: "Quick bottle run",
      detail: "Fill hand tanks at the hatch, touch nothing else",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.8, text: "You fill tanks in the entry chamber and leave the garden as found. It's the politest robbery of your career.",
          effects: { oxygen: 10 } },
        { chance: 0.2, text: "The hatch interlock fights you the whole time — the keeper built it to keep air in, not to share. You come away with less than the trip cost in patience.",
          effects: { oxygen: 4 }, damage: { morale: -2 } }
      ]
    },
    {
      label: "Seal it and go",
      outcomes: [
        { chance: 1, text: "You leave the keeper his garden and dog the hatch behind you. For days afterward, the recycled air tastes noticeably of pipes.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "cache-010",
  title: "Jettison Trail",
  category: "caches",
  text: "Cargo pods strung along your course for a thousand kilometers, jettisoned in a hurry. Somebody lightened ship to run faster. From something. The pods drift like breadcrumbs, and you are choosing to think about the birds later.",
  choices: [
    {
      label: "Scoop the whole string",
      detail: "Sweep every pod, spend the fuel, linger in someone's wake",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.6, text: "Pod after pod of honest cargo: food, water, spare fittings. Whoever ran, ran rich. You choose not to wonder how it ended for them.",
          effects: { food: 12, parts: 5, water: 6 }, delayDays: 1 },
        { chance: 0.25, text: "Half the pods breached on ejection and vacuum-ruined the contents. You sort freeze-dried garbage for a day and keep the survivors.",
          effects: { food: 4 }, damage: { morale: -3 }, delayDays: 1 },
        { chance: 0.15, text: "The sixth pod is rigged — a parting gift for pursuers. The blast peels hull plating and answers the question of why they were running.",
          effects: { parts: 3 }, damage: { hull: -15 }, delayDays: 1 }
      ]
    },
    {
      label: "Grab the nearest pod",
      detail: "One quick snatch, stay on the throttle",
      cost: { fuel: -2 },
      outcomes: [
        { chance: 0.75, text: "One clean pod, decent contents, no drama. You leave the rest of the breadcrumb trail to whatever follows it.",
          effects: { food: 6, parts: 2 } },
        { chance: 0.25, text: "The pod holds someone's personal effects — photographs, a child's boot, letters. Nothing you can burn, eat, or bolt on. The crew is quiet after.",
          effects: { food: 2 }, damage: { morale: -2 } }
      ]
    },
    {
      label: "Don't touch the breadcrumbs",
      outcomes: [
        { chance: 1, text: "You pass the trail at full burn. Whatever they were running from, you'd rather meet it with distance than with its bait in your hold.",
          damage: { morale: -2 } }
      ]
    }
  ]
}
);
