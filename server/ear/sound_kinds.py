"""Merge the 527 AudioSet labels CED outputs into ~30 acoustic kinds.

Kinds are acoustic ("cat", "knock"), not meanings ("danger", "someone is coming"):
the ear reports what it heard; what it means to her is the brain's call.
"speech" and "ignore" are special: speech has its own path, ignore is scene/noise.
"""
import csv

KINDS = {
    "speech": ("说话", ["Speech", "Male speech, man speaking", "Female speech, woman speaking",
                      "Child speech, kid speaking", "Conversation", "Narration, monologue", "Babbling",
                      "Speech synthesizer", "Whispering"]),
    "ignore": ("", ["Silence", "Sine wave", "Harmonic", "Chirp tone", "Sound effect", "Pulse",
                    "Inside, small room", "Inside, large room or hall", "Inside, public space",
                    "Outside, urban or manmade", "Outside, rural or natural", "Reverberation", "Echo",
                    "Noise", "Environmental noise", "Static", "Mains hum", "Distortion", "Sidetone",
                    "Cacophony", "White noise", "Pink noise", "Throbbing", "Vibration", "Field recording",
                    "Hum", "Wind noise (microphone)", "Electronic tuner", "Effects unit", "Chorus effect",
                    "Tap"]),
    "shout": ("喊叫", ["Shout", "Bellow", "Whoop", "Yell", "Battle cry", "Children shouting", "Screaming"]),
    "laugh": ("笑声", ["Laughter", "Baby laughter", "Giggle", "Snicker", "Belly laugh", "Chuckle, chortle"]),
    "cry": ("哭声", ["Crying, sobbing", "Baby cry, infant cry", "Whimper", "Wail, moan"]),
    "cough": ("咳嗽喷嚏", ["Cough", "Throat clearing", "Sneeze", "Sniff"]),
    "snore": ("打鼾", ["Snoring"]),
    "body": ("呼吸咀嚼等", ["Sigh", "Groan", "Grunt", "Breathing", "Wheeze", "Gasp", "Pant", "Snort",
                         "Chewing, mastication", "Biting", "Gargling", "Stomach rumble", "Burping, eructation",
                         "Hiccup", "Fart", "Heart sounds, heartbeat", "Heart murmur"]),
    "footsteps": ("脚步", ["Run", "Shuffle", "Walk, footsteps"]),
    "clap": ("拍手欢呼", ["Hands", "Finger snapping", "Clapping", "Cheering", "Applause"]),
    "crowd": ("人声嘈杂", ["Chatter", "Crowd", "Hubbub, speech noise, speech babble", "Children playing"]),
    "cat": ("猫", ["Cat", "Purr", "Meow", "Hiss", "Caterwaul", "Roaring cats (lions, tigers)"]),
    "dog": ("狗", ["Dog", "Bark", "Yip", "Howl", "Bow-wow", "Growling", "Whimper (dog)",
                  "Canidae, dogs, wolves"]),
    "bird": ("鸟", ["Bird", "Bird vocalization, bird call, bird song", "Chirp, tweet", "Squawk", "Pigeon, dove",
                   "Coo", "Crow", "Caw", "Owl", "Hoot", "Bird flight, flapping wings", "Fowl",
                   "Chicken, rooster", "Cluck", "Crowing, cock-a-doodle-doo", "Turkey", "Gobble", "Duck",
                   "Quack", "Goose", "Honk"]),
    "animal": ("其他动物", ["Animal", "Domestic animals, pets", "Livestock, farm animals, working animals", "Horse",
                        "Clip-clop", "Neigh, whinny", "Cattle, bovinae", "Moo", "Pig", "Oink", "Goat", "Bleat",
                        "Sheep", "Wild animals", "Roar", "Rodents, rats, mice", "Mouse", "Patter", "Insect",
                        "Cricket", "Mosquito", "Fly, housefly", "Buzz", "Bee, wasp, etc.", "Frog", "Croak",
                        "Snake", "Rattle", "Whale vocalization"]),
    "chime": ("铃铛风铃", ["Bell", "Church bell", "Jingle bell", "Bicycle bell", "Chime", "Wind chime",
                       "Change ringing (campanology)", "Cowbell", "Jingle, tinkle", "Ding"]),
    "tv": ("电视广播", ["Television", "Radio"]),
    "doorbell": ("门铃", ["Doorbell", "Ding-dong"]),
    "knock": ("敲门敲击", ["Knock"]),       # "Tap" is in ignore: single light taps aren't worth an event (user, 2026-11-06)
    "door": ("开关门", ["Door", "Sliding door", "Slam", "Cupboard open or close", "Drawer open or close",
                     "Squeak", "Creak"]),
    "kitchen": ("厨房声", ["Dishes, pots, and pans", "Cutlery, silverware", "Chopping (food)", "Frying (food)",
                        "Microwave oven", "Blender", "Boiling", "Sizzle", "Stir", "Chink, clink"]),
    "water": ("水声", ["Water tap, faucet", "Sink (filling or washing)", "Bathtub (filling or washing)",
                     "Toilet flush", "Water", "Liquid", "Splash, splatter", "Slosh", "Squish", "Drip", "Pour",
                     "Trickle, dribble", "Gush", "Fill (with liquid)", "Spray", "Pump (liquid)", "Gurgling",
                     "Steam", "Plop"]),
    "weather": ("风雨火", ["Wind", "Rustling leaves", "Thunderstorm", "Thunder", "Rain", "Raindrop",
                        "Rain on surface", "Stream", "Waterfall", "Ocean", "Waves, surf", "Fire", "Crackle"]),
    "appliance": ("电器机械", ["Hair dryer", "Toothbrush", "Electric toothbrush", "Vacuum cleaner",
                          "Electric shaver, electric razor", "Sewing machine", "Mechanical fan",
                          "Air conditioning", "Printer", "Cash register", "Camera", "Single-lens reflex camera",
                          "Clock", "Tick", "Tick-tock", "Mechanisms", "Ratchet, pawl", "Gears", "Pulleys",
                          "Whir"]),
    "typing": ("键盘打字", ["Typing", "Typewriter", "Computer keyboard", "Writing", "Clicking", "Clickety-clack"]),
    "phone": ("电话铃", ["Telephone", "Telephone bell ringing", "Ringtone", "Telephone dialing, DTMF",
                      "Dial tone", "Busy signal"]),
    "alarm": ("警报提示音", ["Alarm", "Alarm clock", "Siren", "Civil defense siren", "Buzzer",
                        "Smoke detector, smoke alarm", "Fire alarm", "Foghorn", "Whistle", "Steam whistle",
                        "Car alarm", "Emergency vehicle", "Police car (siren)", "Ambulance (siren)",
                        "Fire engine, fire truck (siren)", "Reversing beeps", "Beep, bleep", "Ping", "Sonar"]),
    "crash": ("碰撞巨响", ["Smash, crash", "Breaking", "Shatter", "Glass", "Bang", "Thump, thud", "Thunk", "Clang",
                       "Clatter", "Crack", "Whack, thwack", "Slap, smack", "Crushing", "Bouncing",
                       "Basketball bounce", "Explosion", "Boom", "Burst, pop", "Eruption", "Gunshot, gunfire",
                       "Machine gun", "Fusillade", "Artillery fire", "Cap gun", "Fireworks", "Firecracker",
                       "Wood", "Chop", "Splinter"]),
    "vehicle": ("车辆交通", ["Vehicle", "Boat, Water vehicle", "Sailboat, sailing ship", "Rowboat, canoe, kayak",
                         "Motorboat, speedboat", "Ship", "Motor vehicle (road)", "Car",
                         "Vehicle horn, car horn, honking", "Toot", "Power windows, electric windows", "Skidding",
                         "Tire squeal", "Car passing by", "Race car, auto racing", "Truck", "Air brake",
                         "Air horn, truck horn", "Ice cream truck, ice cream van", "Bus", "Motorcycle",
                         "Traffic noise, roadway noise", "Rail transport", "Train", "Train whistle", "Train horn",
                         "Railroad car, train wagon", "Train wheels squealing", "Subway, metro, underground",
                         "Aircraft", "Aircraft engine", "Jet engine", "Propeller, airscrew", "Helicopter",
                         "Fixed-wing aircraft, airplane", "Bicycle", "Skateboard", "Engine",
                         "Light engine (high frequency)", "Medium engine (mid frequency)",
                         "Heavy engine (low frequency)", "Engine knocking", "Engine starting", "Idling",
                         "Accelerating, revving, vroom"]),
    "tools": ("工具施工", ["Tools", "Hammer", "Jackhammer", "Sawing", "Filing (rasp)", "Sanding", "Power tool",
                       "Drill", "Dental drill, dentist's drill", "Lawn mower", "Chainsaw"]),
    "rustle": ("窸窣", ["Rustle", "Crumpling, crinkling", "Tearing", "Zipper (clothing)", "Keys jangling",
                      "Coin (dropping)", "Scissors", "Shuffling cards", "Scratch", "Scrape", "Rub", "Roll", "Flap",
                      "Whip", "Whoosh, swoosh, swish", "Arrow", "Squeal", "Zing", "Boing", "Crunch"]),
    "music": ("音乐唱歌", ["Singing", "Choir", "Yodeling", "Chant", "Mantra", "Male singing", "Female singing",
                       "Child singing", "Synthetic singing", "Rapping", "Humming", "Whistling"]),
}
OTHER = ("other", "其他")

# Labels that mean "a human voice" (speech or not): speech, shout, laugh, cry, singing/humming/chant,
# sighs and groans. Breathing and coughs are body sounds, not voice. Used for voice-likeness:
# on the user's recordings (2026-11-06) real speech scored 0.53-0.85 with CED-mini, misfires <= 0.06.
VOICE = (set(KINDS["speech"][1]) | set(KINDS["shout"][1]) | set(KINDS["laugh"][1]) | set(KINDS["cry"][1])
         | {"Singing", "Choir", "Yodeling", "Chant", "Mantra", "Male singing", "Female singing",
            "Child singing", "Synthetic singing", "Rapping", "Humming", "Sigh", "Groan", "Grunt"})
LABEL_ZH = {k: v[0] for k, v in KINDS.items()} | {OTHER[0]: OTHER[1]}


def build(csv_path):
    """Return {AudioSet label: kind}. The whole Music subtree (contiguous in the ontology,
    "Music" .. "Scary music") maps to "music" unless listed explicitly above."""
    names = [r[2] for r in list(csv.reader(open(csv_path, encoding="utf-8")))[1:]]
    explicit = {lab: kind for kind, (_, labs) in KINDS.items() for lab in labs}
    out = {}
    try:
        lo, hi = names.index("Music"), names.index("Scary music")
    except ValueError:
        lo, hi = -1, -2
    for i, n in enumerate(names):
        out[n] = explicit.get(n) or ("music" if lo <= i <= hi else OTHER[0])
    return out


if __name__ == "__main__":
    import collections
    import sys
    m = build(sys.argv[1])
    explicit = {lab for _, (_, labs) in KINDS.items() for lab in labs}
    missing = sorted(explicit - set(m))
    print("labels:", len(m), "| explicit names not found in csv:", missing or "none")
    c = collections.Counter(m.values())
    print(" ".join(f"{k}:{n}" for k, n in sorted(c.items(), key=lambda t: -t[1])))
    print("other ->", [n for n, k in m.items() if k == "other"])
