/* ORION TRAIL — anomalies & signals. Prefix: anom- */
SCENARIOS.push(
{
  id: "anom-001",
  title: "The Repeating Signal",
  category: "anomalies",
  text: "Sensors catch a signal from an empty patch of nothing: seven tones, repeating, mathematically clean. Too regular for nature. Too old for anyone you know. It is exactly where you would put a trap, or a miracle.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Divert and investigate",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.4, text: "An ancient probe, pre-Collapse, still faithfully broadcasting. Its power core alone is worth the detour.",
          effects: { power: 20, parts: 2 }, repair: { morale: 5 } },
        { chance: 0.4, text: "Nothing. The signal stops the moment you arrive, like a game of cosmic doorbell-ditch.",
          effects: {}, damage: { morale: -6 } },
        { chance: 0.2, text: "The 'signal' is a navigation hazard beacon. You learn this the way beacons intend: almost.",
          damage: { hull: -12, sensors: -8 } }
      ]
    },
    {
      label: "Log it and hold course",
      outcomes: [
        { chance: 1, text: "You mark the coordinates for whoever comes after. The seven tones follow you to sleep for a week.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "anom-002",
  title: "The Lens That Isn't There",
  category: "anomalies",
  text: "Starlight ahead bends around a point your sensors swear is empty vacuum. Textbook gravitational lensing, minus the gravity, minus the thing. Something massive is sitting there being invisible, or physics has developed a sense of humor. Neither possibility improves your day.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Thread the lens for a slingshot",
      detail: "Free velocity, if the invisible mass cooperates",
      cost: { fuel: -4 },
      outcomes: [
        { chance: 0.35, text: "It works. Whatever isn't there flings you down the trail like a friend you never met.",
          effects: { fuel: 18 } },
        { chance: 0.4, text: "Halfway through, the lensing simply stops. You coast out the other side, unflung, feeling watched.",
          damage: { morale: -5 } },
        { chance: 0.25, text: "Tidal shear grabs the ship like a dog with a chew toy. The invisible thing, it turns out, has opinions.",
          damage: { hull: -15, engines: -10 } }
      ]
    },
    {
      label: "Map it from a safe distance",
      detail: "Requires working sensors",
      requires: { systemOk: ["sensors"] },
      cost: { power: -5 },
      outcomes: [
        { chance: 0.6, text: "The lens makes a superb calibration target. Your charts have never been sharper about a thing that isn't there.",
          repair: { astrogation: 10 } },
        { chance: 0.4, text: "Mid-scan, the lensing winks off. Your instruments record twelve minutes of impossible, then decline to comment.",
          damage: { morale: -4 } }
      ]
    },
    {
      label: "Go around it",
      outcomes: [
        { chance: 1, text: "You buy two degrees of detour and a lifetime of wondering. The stars behind you stay bent for longer than they should.",
          effects: { fuel: -3 }, damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "anom-003",
  title: "The Sphere",
  category: "anomalies",
  text: "An asteroid, spherical to nine decimal places. Nature does not do that. Machining does that. It sits in the rubble, smooth and patient, like a marble somebody very large put down and meant to come back for.",
  zones: ["belt", "outer"],
  choices: [
    {
      label: "Land and drill a core sample",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.35, text: "Dense, uniform nickel-iron all the way down. Boring, priceless, and mercifully explicable. The refinery hums for days.",
          effects: { parts: 4, power: 10 } },
        { chance: 0.4, text: "The drill skips off the surface like it's ashamed of itself. Nine decimal places of nothing to show for it.",
          damage: { morale: -5 } },
        { chance: 0.25, text: "At two meters' depth the sphere rings like a bell. Everyone aboard hears it in their teeth. You stop drilling.",
          damage: { morale: -10, sensors: -10 } }
      ]
    },
    {
      label: "Survey it from standoff range",
      detail: "Requires working sensors",
      requires: { systemOk: ["sensors"] },
      cost: { power: -5 },
      outcomes: [
        { chance: 0.6, text: "Solid, inert, geologically dull. You log it as 'natural,' loudly, and everyone pretends to be convinced.",
          repair: { morale: 4 } },
        { chance: 0.4, text: "The returns say hollow. The returns say a centered cavity. The returns get filed under 'instrument error' by unanimous vote.",
          damage: { morale: -6 } }
      ]
    },
    {
      label: "Leave the marble alone",
      outcomes: [
        { chance: 1, text: "Somebody put it there. Somebody may retrieve it. You'd rather not be present for either half of that sentence.",
          damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "anom-004",
  title: "Yesterday, Again",
  category: "anomalies",
  text: "You cross an invisible line and every sensor reads yesterday: yesterday's star positions, yesterday's hull temperature, yesterday's everything. The instruments aren't broken. They're wrong in perfect agreement. Somewhere behind you, presumably, is a patch of space that reads tomorrow.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Hold position and recalibrate",
      detail: "A day lost, maybe more gained",
      outcomes: [
        { chance: 0.4, text: "Working against a known-wrong baseline turns out to be superb calibration. The sensors come back sharper than the day they shipped.",
          delayDays: 1, repair: { sensors: 20 } },
        { chance: 0.4, text: "At 0300 the readings snap to now, all at once, unprompted. You learn nothing except how loud a quiet bridge can be.",
          delayDays: 1, damage: { morale: -4 } },
        { chance: 0.2, text: "The sensors develop a taste for the past. Every few hours they drift back a day, wistfully, and have to be dragged home.",
          delayDays: 1, damage: { sensors: -20 } }
      ]
    },
    {
      label: "Punch through at full burn",
      cost: { fuel: -5 },
      outcomes: [
        { chance: 0.7, text: "Twenty minutes of flying yesterday, then clean readings. Nobody logs it. The log would only say it happened yesterday anyway.",
          damage: { morale: -3 } },
        { chance: 0.3, text: "You exit clean, but astrogation has faithfully recorded a day that never happened, and refuses to un-know it.",
          damage: { astrogation: -12 }, delayDays: 1 }
      ]
    }
  ]
},
{
  id: "anom-005",
  title: "St. Elmo's Nothing",
  category: "anomalies",
  text: "A ball of plasma, fist-sized and self-sustaining, drifts alongside the ship. Nothing for it to burn, no field to hold it, no reason for it to exist. It keeps pace at exactly your speed, which is either coincidence or interest.",
  zones: ["belt", "outer"],
  choices: [
    {
      label: "Capture it in a containment rig",
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.3, text: "It sits in the magnetic bottle, burning on nothing, and bleeds clean power into the grid. You do not ask it how.",
          effects: { power: 25 } },
        { chance: 0.45, text: "The instant the bottle seals, it winks out. Not dissipates — winks out, like a light switch you can't see.",
          damage: { morale: -5 } },
        { chance: 0.25, text: "It discharges through the rig, the conduit, and the generator, in that order, with what feels like deliberation.",
          effects: { power: -10 }, damage: { generator: -15 } }
      ]
    },
    {
      label: "Study it from the viewport",
      detail: "Requires working sensors",
      requires: { systemOk: ["sensors"] },
      cost: { power: -5 },
      outcomes: [
        { chance: 0.5, text: "Its emission spectrum suggests a containment trick your generator can borrow. The fireball is a better engineer than you.",
          effects: { power: 12 } },
        { chance: 0.5, text: "The spectral data is noise. But plotted over time, the noise repeats. You archive the file and stop plotting things.",
          damage: { morale: -5 } }
      ]
    },
    {
      label: "Vent thrusters and shoo it away",
      cost: { fuel: -3 },
      outcomes: [
        { chance: 1, text: "It drifts off without protest. For three days the crew argues about what it was. Nobody argues that it wasn't.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "anom-006",
  title: "The Warm Wanderer",
  category: "anomalies",
  text: "A rogue planet, sunless, starless, glowing dull red at the seams. Its core never got the memo about dying. Volcanic vents bleed heat and volatiles into the dark — a free fuel depot, if 'free' ever meant anything out here.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Descend and harvest the vents",
      detail: "Big haul, angry geology",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 0.45, text: "The vents cooperate. Tanks full of cracked volatiles, water squeezed from ancient ice. The planet keeps wandering. So do you.",
          effects: { fuel: 30, water: 15 } },
        { chance: 0.35, text: "A thin harvest, and the ash cloud sandblasts your hull on the climb out. The planet's generosity is strictly rhetorical.",
          effects: { fuel: 10 }, damage: { hull: -8 } },
        { chance: 0.2, text: "The vent field shrugs mid-harvest — a quake with no sun to blame. You leave fast, low, and dented.",
          effects: { fuel: 5 }, damage: { hull: -20 } }
      ]
    },
    {
      label: "Skim the outgassing halo from orbit",
      cost: { fuel: -3 },
      outcomes: [
        { chance: 1, text: "The scoops sip the halo — a modest take, and the corrosive haze etches the sensor domes as a service fee.",
          effects: { fuel: 10 }, damage: { sensors: -6 } }
      ]
    },
    {
      label: "Fly on past",
      outcomes: [
        { chance: 1, text: "A warm world with nobody's name on it recedes behind you. The fuel gauge doesn't say anything. It doesn't have to.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "anom-007",
  title: "Her Voice",
  category: "anomalies",
  text: "The comm crackles and speaks in your navigator's mother's voice, using her pet name for him, asking him to come home. His mother has been dead nine years. The source is forty degrees off course, in nothing, patiently repeating.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Divert to the source",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.3, text: "A colony ship's memorial buoy, drifting for decades, broadcasting archived family messages at whoever matches. It found a match. There's salvage, and something like closure.",
          effects: { meds: 2, parts: 1 }, repair: { morale: 8 } },
        { chance: 0.4, text: "Nothing there. As you arrive, the voice says thank you for coming, and stops. Your navigator requests the night shift, permanently.",
          damage: { morale: -8 } },
        { chance: 0.3, text: "Empty space. But the voice keeps transmitting behind you for three days, and your sensors log it as coming from inside the hull.",
          damage: { morale: -10, sensors: -8 } }
      ]
    },
    {
      label: "Jam the frequency",
      cost: { power: -5 },
      outcomes: [
        { chance: 1, text: "Silence. Your navigator doesn't speak for a week. When he does, it's to ask whether jamming a voice counts as hanging up on it.",
          damage: { morale: -5 } }
      ]
    },
    {
      label: "Let it play, hold course",
      outcomes: [
        { chance: 0.5, text: "The crew adopts it, strangely, like a fireplace. A dead woman calls someone home every night and everyone sleeps a little better.",
          repair: { morale: 5 } },
        { chance: 0.5, text: "On the fourth day it starts using other crew members' childhood names, on channels you'd powered down. You purge the whole comm stack. It takes two tries.",
          damage: { morale: -10, sensors: -6 } }
      ]
    }
  ]
},
{
  id: "anom-008",
  title: "Somebody Else's Voyager",
  category: "anomalies",
  text: "The object is a probe, and it is not one of ours — the geometry is wrong in ways your eyes keep sliding off. First contact, or very old litter from a neighborhood that moved away. It's light. It would fit in the cargo bay.",
  zones: ["deep"],
  choices: [
    {
      label: "Bring it aboard",
      outcomes: [
        { chance: 0.35, text: "The casing yields alloys your tools shouldn't be able to cut but do, apologetically. The materials alone rewrite half your spares inventory.",
          effects: { parts: 5 }, repair: { sensors: 10 } },
        { chance: 0.4, text: "It sits in the bay doing nothing. The crew develops a rotation of checking that it's still doing nothing. It always is. So far.",
          damage: { morale: -6 } },
        { chance: 0.25, text: "It emits a single pulse. Every screen on the ship shows the same coordinates for one second. Astrogation now disagrees with itself about where you are.",
          damage: { astrogation: -15, morale: -6 } }
      ]
    },
    {
      label: "Full survey, hands off",
      detail: "Requires working sensors",
      requires: { systemOk: ["sensors"] },
      cost: { power: -6 },
      outcomes: [
        { chance: 1, text: "You document every impossible centimeter and leave it drifting. You will be famous, later, if there is a later.",
          repair: { morale: 4 } }
      ]
    },
    {
      label: "Give it a wide berth",
      outcomes: [
        { chance: 1, text: "You alter course around a piece of litter, respectfully, the way you'd step around a sleeping dog of unknown size.",
          effects: { fuel: -3 }, damage: { morale: -3 } }
      ]
    }
  ]
},
{
  id: "anom-009",
  title: "The Song",
  category: "anomalies",
  text: "A radio source is singing — actual melody, minor key, older than agriculture by the redshift. Over hours it resolves into something like a lullaby. The crew starts humming it. All of them. In unison. Nobody noticed when they started.",
  zones: ["outer", "deep"],
  choices: [
    {
      label: "Trace it to the source",
      cost: { fuel: -6 },
      outcomes: [
        { chance: 0.3, text: "A hollow crystalline asteroid, ringing in the stellar wind. Natural, beautiful, and mineable. The universe wrote a song and you get royalties.",
          effects: { parts: 3 }, repair: { morale: 8 } },
        { chance: 0.45, text: "The song fades as you approach and resumes behind you, from where you just were. You decline to play tag with it.",
          damage: { morale: -6 } },
        { chance: 0.25, text: "The source is empty space, and your recordings captured nothing. The crew now disagrees, violently, about how the melody went.",
          damage: { morale: -12 } }
      ]
    },
    {
      label: "Broadcast it back at them",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.5, text: "The song shifts key to harmonize with your echo. It's a duet now. The crew sleeps better than they have in months, and doesn't ask why.",
          repair: { morale: 8 } },
        { chance: 0.5, text: "The song stops. All bands go silent — not quiet, silent, a full day of it. The sensors sulk. So does everyone else.",
          damage: { sensors: -8, morale: -6 } }
      ]
    },
    {
      label: "Comms blackout, full ahead",
      outcomes: [
        { chance: 1, text: "You silence every receiver. The humming continues for five more days, in unison, without the song. You do not point this out to anyone.",
          damage: { morale: -5 } }
      ]
    }
  ]
},
{
  id: "anom-010",
  title: "The Extra Star",
  category: "anomalies",
  text: "Astrogation flags a star that isn't in any chart — bright, close, and spectrally impossible: it's on the wrong side of its own lifespan. The charts are never wrong. The charts are currently wrong. Your navigator wants to file a complaint with the universe.",
  zones: ["deep"],
  choices: [
    {
      label: "Recalibrate the charts around it",
      detail: "A day parked, doing math",
      outcomes: [
        { chance: 0.5, text: "You rebuild the reference frame with the impossible star as an anchor. It works beautifully. You agree never to ask why.",
          delayDays: 1, repair: { astrogation: 20 } },
        { chance: 0.3, text: "The math refuses to close. You split the difference, trust neither chart nor star, and fly on like an honest agnostic.",
          delayDays: 1, damage: { morale: -4 } },
        { chance: 0.2, text: "Mid-calibration, the star vanishes — taking your half-built reference frame with it. The old charts smugly refuse to load clean.",
          delayDays: 1, damage: { astrogation: -20 } }
      ]
    },
    {
      label: "Trust the charts, ignore the sky",
      outcomes: [
        { chance: 0.6, text: "Nothing comes of it. The navigator sulks for a week about epistemology, which is at least a quiet way to sulk.",
          damage: { morale: -3 } },
        { chance: 0.4, text: "The charts were keyed to stars that agree with the impostor. Your course drifts wide before anyone admits it.",
          delayDays: 2, effects: { fuel: -5 } }
      ]
    }
  ]
},
{
  id: "anom-011",
  title: "The Hole in the Sky",
  category: "anomalies",
  text: "Ahead, a circle of perfect black — no stars, no background hiss, no anything. Not an object blocking light: an absence of there being light to block. It is seven kilometers wide, and your course runs straight through the middle of it.",
  zones: ["deep"],
  choices: [
    {
      label: "EVA team to the boundary",
      detail: "Somebody has to touch it",
      requires: { minCrew: 3 },
      cost: { parts: -1 },
      outcomes: [
        { chance: 0.35, text: "The boundary is a film of ultracold dust, lensing everything to black. Samples, data, and a mystery dying the good mundane death.",
          effects: { parts: 3 }, repair: { morale: 6 } },
        { chance: 0.5, text: "The team returns. Their instruments recorded blank. Their suit cams recorded blank. Their memories of the last hour are also blank.",
          damage: { morale: -8, sensors: -5 } },
        { chance: 0.15, text: "Two tethers come back taut. The third comes back cut — clean, polished, and slightly colder than the others.",
          crewDeath: true, damage: { morale: -15 } }
      ]
    },
    {
      label: "Burn around it",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 1, text: "You keep the black circle in the rear cameras until it's gone. Nobody suggests doing otherwise. Nobody suggests much of anything.",
          damage: { morale: -3 } }
      ]
    },
    {
      label: "Fly straight through",
      outcomes: [
        { chance: 0.5, text: "Eleven seconds of nothing — no stars, no telemetry, no engine noise. Every clock aboard disagrees about how long it took.",
          damage: { morale: -6, astrogation: -8 } },
        { chance: 0.5, text: "Nothing happens. Nothing at all. You emerge into starlight feeling like something checked your papers and found them boring.",
          damage: { morale: -4 } }
      ]
    }
  ]
},
{
  id: "anom-012",
  title: "Outriders",
  category: "anomalies",
  text: "Three lights off the port bow, holding formation with you. Not reflections — they show parallax. They accelerate when you accelerate, exactly, with no drive signature. Six hours now. The night shift has stopped mentioning them, which is how you know everyone is scared.",
  zones: ["belt", "outer"],
  choices: [
    {
      label: "Hail them on all bands",
      cost: { power: -5 },
      outcomes: [
        { chance: 0.3, text: "The reply is your own hail, played back — along with three days of internal chatter, including things said nowhere near a comm.",
          damage: { morale: -10 } },
        { chance: 0.5, text: "No answer — but as they peel away in unison, one pulses a burst of clean navigational data. A tip, maybe. Or a receipt.",
          repair: { astrogation: 8 } },
        { chance: 0.2, text: "The lights merge into one, brighten, and vanish. Your sensors insist nothing was ever there, and now flinch at Jupiter.",
          damage: { sensors: -10, morale: -8 } }
      ]
    },
    {
      label: "Hard burn to lose them",
      cost: { fuel: -8 },
      outcomes: [
        { chance: 1, text: "They don't chase. They just stop, hold station, and recede — three lights watching you spend fuel on dignity.",
          damage: { morale: -3 } }
      ]
    },
    {
      label: "Log them and ignore them",
      outcomes: [
        { chance: 0.6, text: "Gone by morning shift. The log entry reads 'optical phenomenon.' The word 'optical' is doing heroic, unpaid work.",
          damage: { morale: -4 } },
        { chance: 0.4, text: "They pace you for six more days. Sleep aboard becomes theoretical. On the seventh day they leave, and that's somehow worse.",
          damage: { morale: -8 } }
      ]
    }
  ]
}
);
