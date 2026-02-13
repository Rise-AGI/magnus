# back_end/server/_file_custody_manager.py
import os
import time
import shutil
import random
import asyncio
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Tuple, List, BinaryIO
from dataclasses import dataclass

from ._magnus_config import magnus_config
from ._resource_manager import _parse_size_string

logger = logging.getLogger(__name__)

FILE_SECRET_PREFIX = "magnus-secret:"
_COPY_CHUNK_SIZE = 64 * 1024


def _format_size(size_bytes: int) -> str:
    value = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024:
            return f"{value:g} {unit}"
        value /= 1024
    return f"{value:g} PB"


class FileTooLargeError(Exception):
    def __init__(self, filename: str, limit: int):
        self.filename = filename
        self.limit = limit
        super().__init__(f'File "{filename}" exceeds the {_format_size(limit)} limit.')


# === Human-friendly token generation ===

def _sieve_primes(lo: int, hi: int) -> List[int]:
    sieve = bytearray(b'\x01') * (hi + 1)
    sieve[0] = sieve[1] = 0
    for i in range(2, int(hi**0.5) + 1):
        if sieve[i]:
            sieve[i*i::i] = b'\x00' * len(sieve[i*i::i])
    return [p for p in range(lo, hi + 1) if sieve[p]]

_PRIMES = _sieve_primes(1000, 99999)

_WORDS = [
    "able", "acid", "aged", "also", "arch", "area", "army", "away",
    "back", "ball", "band", "bank", "barn", "base", "bath", "beam",
    "bear", "beat", "bell", "belt", "bend", "bird", "bite", "blow",
    "blue", "blur", "boat", "body", "bold", "bolt", "bomb", "bond",
    "bone", "book", "born", "boss", "bowl", "bulk", "burn", "bush",
    "busy", "cafe", "cage", "cake", "call", "calm", "came", "camp",
    "cape", "card", "care", "cart", "case", "cash", "cast", "cave",
    "cell", "chat", "chip", "city", "clam", "clan", "clay", "clip",
    "club", "clue", "coal", "coat", "code", "coil", "coin", "cold",
    "colt", "come", "cook", "cool", "cope", "copy", "cord", "core",
    "corn", "cost", "cozy", "crew", "crop", "crow", "cube", "curl",
    "cute", "dale", "dame", "dare", "dark", "dart", "dash", "data",
    "dawn", "deal", "dear", "debt", "deck", "deep", "deer", "demo",
    "deny", "desk", "dial", "dice", "diet", "dirt", "disc", "dish",
    "dock", "does", "done", "doom", "door", "dose", "dove", "down",
    "draw", "drew", "drop", "drum", "dual", "duck", "dude", "duke",
    "dull", "dump", "dune", "dusk", "dust", "duty", "each", "earl",
    "earn", "ease", "east", "easy", "edge", "edit", "else", "emit",
    "epic", "even", "ever", "evil", "exam", "exit", "face", "fact",
    "fade", "fail", "fair", "fake", "fall", "fame", "fang", "fare",
    "farm", "fast", "fate", "fear", "feat", "feed", "feel", "feet",
    "fell", "felt", "file", "fill", "film", "find", "fine", "fire",
    "firm", "fish", "fist", "five", "flag", "flat", "fled", "flew",
    "flip", "flow", "foam", "fold", "folk", "fond", "font", "food",
    "fool", "foot", "ford", "fork", "form", "fort", "foul", "four",
    "free", "from", "fuel", "full", "fund", "fury", "fuse", "gain",
    "gale", "game", "gang", "gate", "gave", "gaze", "gear", "gene",
    "gift", "girl", "give", "glad", "glow", "glue", "goal", "goat",
    "goes", "gold", "golf", "gone", "good", "grab", "gram", "gray",
    "grew", "grid", "grim", "grin", "grip", "grow", "gulf", "guru",
    "hack", "hair", "half", "hall", "halt", "hand", "hang", "hard",
    "harm", "harp", "hate", "haul", "have", "hawk", "haze", "head",
    "heal", "heap", "hear", "heat", "heel", "held", "help", "herb",
    "herd", "here", "hero", "hide", "high", "hike", "hill", "hint",
    "hire", "hold", "hole", "home", "hood", "hook", "hope", "horn",
    "host", "hour", "huge", "hull", "hung", "hunt", "hurt", "icon",
    "idea", "inch", "into", "iron", "isle", "item", "jack", "jade",
    "jail", "jazz", "join", "joke", "jump", "june", "jury", "just",
    "keen", "keep", "kept", "kick", "kill", "kind", "king", "kiss",
    "kite", "knee", "knew", "knit", "knob", "knot", "know", "lace",
    "lack", "laid", "lake", "lamp", "land", "lane", "lark", "last",
    "late", "lawn", "lazy", "lead", "leaf", "lean", "leap", "left",
    "lend", "lens", "less", "levy", "lied", "life", "lift", "like",
    "lime", "limp", "line", "link", "lion", "list", "live", "load",
    "loan", "lock", "loft", "logo", "lone", "long", "look", "loop",
    "lord", "lose", "loss", "lost", "love", "luck", "lump", "lure",
    "lurk", "lush", "made", "mail", "main", "make", "male", "mall",
    "malt", "mane", "many", "mare", "mark", "mars", "mask", "mass",
    "mate", "maze", "meal", "mean", "meat", "meet", "melt", "memo",
    "mend", "menu", "mere", "mesh", "mile", "milk", "mill", "mind",
    "mine", "mint", "miss", "mist", "mode", "mold", "mood", "moon",
    "more", "moss", "most", "moth", "move", "much", "mule", "muse",
    "must", "myth", "nail", "name", "navy", "near", "neat", "neck",
    "need", "nest", "news", "next", "nice", "nine", "node", "none",
    "norm", "nose", "note", "noun", "nova", "obey", "odds", "okay",
    "olive", "once", "only", "onto", "open", "oral", "oven", "over",
    "owed", "pace", "pack", "page", "paid", "pair", "pale", "palm",
    "pane", "para", "park", "part", "pass", "past", "path", "peak",
    "pear", "peel", "peer", "pier", "pile", "pine", "pink", "pipe",
    "plan", "play", "plea", "plot", "plug", "plum", "plus", "poem",
    "poet", "pole", "poll", "pond", "pool", "pope", "pork", "port",
    "pose", "post", "pour", "pray", "prey", "prop", "pull", "pulp",
    "pump", "pure", "push", "quit", "quiz", "race", "rack", "rage",
    "raid", "rail", "rain", "rare", "rate", "read", "real", "rear",
    "reef", "reel", "rely", "rent", "rest", "rice", "rich", "ride",
    "ring", "riot", "rise", "risk", "road", "roam", "rock", "rode",
    "role", "roll", "roof", "room", "root", "rope", "rose", "ruin",
    "rule", "rush", "rust", "safe", "sage", "said", "sail", "sake",
    "sale", "salt", "same", "sand", "sang", "save", "seal", "seat",
    "seed", "seek", "seem", "seen", "self", "sell", "send", "sent",
    "shed", "shin", "ship", "shop", "shot", "show", "shut", "sick",
    "side", "sigh", "sign", "silk", "sink", "site", "size", "skin",
    "slam", "slap", "slew", "slid", "slim", "slip", "slot", "slow",
    "snap", "snow", "soak", "soar", "sock", "sofa", "soft", "soil",
    "sold", "sole", "some", "song", "soon", "sort", "soul", "span",
    "spin", "spit", "spot", "spur", "star", "stay", "stem", "step",
    "stir", "stop", "such", "suit", "sung", "sure", "surf", "swan",
    "swap", "swim", "tack", "tail", "take", "tale", "talk", "tall",
    "tank", "tape", "task", "taxi", "team", "tear", "tell", "tend",
    "tent", "term", "test", "text", "than", "them", "then", "thin",
    "thus", "tick", "tide", "tidy", "tied", "tier", "tile", "till",
    "tilt", "time", "tiny", "tire", "toad", "toil", "told", "toll",
    "tomb", "tone", "took", "tool", "tops", "tore", "torn", "tour",
    "town", "trap", "tray", "tree", "trek", "trim", "trio", "trip",
    "true", "tube", "tuck", "tuna", "tune", "turf", "turn", "twin",
    "type", "ugly", "unit", "upon", "urge", "used", "user", "vale",
    "vane", "vary", "vast", "veil", "vein", "vent", "verb", "very",
    "vest", "veto", "view", "vine", "void", "volt", "vote", "wade",
    "wage", "wait", "wake", "walk", "wall", "wand", "ward", "warm",
    "warn", "warp", "wash", "wave", "weak", "wear", "weed", "week",
    "well", "went", "were", "west", "what", "whom", "wide", "wife",
    "wild", "will", "wilt", "wind", "wine", "wing", "wire", "wise",
    "wish", "with", "woke", "wolf", "wood", "wool", "word", "wore",
    "work", "worm", "worn", "wrap", "yard", "yarn", "year", "yell",
    "yoga", "yolk", "your", "zeal", "zero", "zinc", "zone", "zoom",
    "about", "above", "abuse", "acute", "admit", "adopt", "adult",
    "after", "again", "agent", "agree", "ahead", "alarm", "album",
    "alien", "align", "alike", "alive", "alley", "allow", "alone",
    "along", "alter", "ample", "angel", "angle", "angry", "ankle",
    "apart", "apple", "apply", "arena", "argue", "arise", "aside",
    "asset", "atlas", "avoid", "awake", "award", "aware", "badge",
    "baker", "basic", "basin", "basis", "batch", "beach", "beast",
    "begin", "being", "below", "bench", "berry", "birth", "black",
    "blade", "blame", "bland", "blank", "blast", "blaze", "bleak",
    "bleed", "blend", "bless", "blind", "block", "bloom", "blown",
    "blues", "bluff", "blunt", "board", "bonus", "boost", "bound",
    "brain", "brand", "brave", "bread", "break", "breed", "brick",
    "bride", "brief", "bring", "brisk", "broad", "broke", "brook",
    "brush", "buddy", "build", "built", "bunch", "burst", "buyer",
    "cable", "camel", "candy", "cargo", "carry", "catch", "cause",
    "cedar", "chain", "chair", "chalk", "charm", "chase", "cheap",
    "check", "cheek", "cheer", "chess", "chest", "chief", "child",
    "china", "chord", "chunk", "civic", "civil", "claim", "clash",
    "clasp", "class", "clean", "clear", "clerk", "click", "cliff",
    "climb", "cling", "cloak", "clock", "clone", "close", "cloth",
    "cloud", "coach", "coast", "cobra", "comet", "coral", "could",
    "count", "couch", "court", "cover", "crack", "craft", "crane",
    "crash", "crazy", "cream", "creek", "crest", "crime", "crisp",
    "cross", "crowd", "crown", "cruel", "crush", "curve", "cycle",
    "daily", "dairy", "dance", "dealt", "death", "debug", "decay",
    "decor", "decoy", "delay", "delta", "demon", "dense", "depth",
    "derby", "devil", "diary", "digit", "dirty", "disco", "ditch",
    "diver", "dizzy", "donor", "doubt", "dough", "draft", "drain",
    "drake", "drama", "drank", "drape", "drawn", "dream", "dress",
    "dried", "drift", "drill", "drink", "drive", "drove", "drugs",
    "dryer", "dying", "eager", "early", "earth", "eight", "elbow",
    "elder", "elect", "elite", "embed", "ember", "empty", "enemy",
    "enjoy", "enter", "entry", "equal", "equip", "error", "essay",
    "event", "every", "exact", "exert", "exist", "extra", "fable",
    "facet", "faith", "false", "fault", "feast", "fence", "ferry",
    "fever", "fiber", "field", "fifth", "fifty", "fight", "final",
    "first", "fixed", "flame", "flank", "flare", "flash", "flask",
    "fleet", "flesh", "flick", "fling", "flint", "float", "flock",
    "flood", "floor", "flora", "flour", "fluid", "flush", "flute",
    "focal", "focus", "force", "forge", "forth", "forum", "found",
    "frame", "frank", "fraud", "fresh", "front", "frost", "froze",
    "fruit", "fungi", "funny", "ghost", "giant", "given", "gland",
    "glass", "gleam", "glide", "globe", "gloom", "glory", "gloss",
    "glove", "going", "grace", "grade", "grain", "grand", "grant",
    "graph", "grasp", "grass", "grave", "gravel", "great", "green",
    "greet", "grief", "grill", "grind", "gross", "group", "grove",
    "grown", "guard", "guess", "guest", "guide", "guild", "guilt",
    "habit", "happy", "harsh", "haste", "haven", "heard", "heart",
    "heavy", "hedge", "hello", "hence", "herbs", "honey", "honor",
    "horse", "hotel", "house", "human", "humor", "ideal", "image",
    "imply", "index", "indie", "infer", "inner", "input", "ivory",
    "jewel", "joint", "joker", "jolly", "judge", "juice", "karma",
    "kayak", "knife", "knock", "known", "label", "labor", "large",
    "laser", "later", "laugh", "layer", "learn", "lease", "leave",
    "legal", "lemon", "level", "lever", "light", "limit", "linen",
    "liner", "llama", "lodge", "logic", "loose", "lover", "lower",
    "loyal", "lucid", "lunar", "lunch", "magic", "major", "manor",
    "maple", "march", "match", "mayor", "medal", "media", "mercy",
    "merge", "merit", "metal", "meter", "might", "minor", "minus",
    "mixed", "model", "money", "month", "moral", "mossy", "motif",
    "motor", "mound", "mount", "mourn", "mouse", "mouth", "movie",
    "muddy", "music", "nerve", "never", "night", "noble", "noise",
    "north", "noted", "novel", "nurse", "occur", "ocean", "offer",
    "often", "onset", "opera", "orbit", "order", "organ", "other",
    "ought", "outer", "oxide", "ozone", "paint", "panel", "panic",
    "paper", "patch", "pause", "peace", "peach", "pearl", "penny",
    "phase", "phone", "photo", "piano", "piece", "pilot", "pitch",
    "pixel", "pizza", "place", "plain", "plant", "plate", "plaza",
    "plead", "plumb", "plume", "plump", "point", "polar", "poppy",
    "porch", "pouch", "pound", "power", "press", "price", "pride",
    "prime", "print", "prior", "prize", "probe", "prone", "proof",
    "proud", "prove", "proxy", "psalm", "pulse", "punch", "pupil",
    "purse", "queen", "quest", "queue", "quick", "quiet", "quota",
    "quote", "radar", "radio", "raise", "rally", "ranch", "range",
    "rapid", "ratio", "reach", "react", "realm", "rebel", "refer",
    "reign", "relax", "relay", "repay", "reply", "rider", "ridge",
    "rifle", "right", "rigid", "risky", "rival", "river", "robin",
    "robot", "rocky", "rouge", "rough", "round", "route", "royal",
    "rugby", "ruler", "rumor", "rural", "saint", "salad", "scale",
    "scare", "scene", "scent", "scope", "score", "scout", "screw",
    "seize", "sense", "serve", "setup", "seven", "shade", "shall",
    "shame", "shape", "share", "shark", "sharp", "shear", "sheep",
    "sheer", "sheet", "shelf", "shell", "shift", "shire", "shirt",
    "shock", "shore", "short", "shout", "siege", "sight", "sigma",
    "since", "sixth", "sixty", "skill", "skull", "slate", "sleep",
    "slice", "slide", "slope", "smart", "smell", "smile", "smith",
    "smoke", "snack", "snake", "solar", "solid", "solve", "sonic",
    "sorry", "south", "space", "spare", "spark", "speak", "spear",
    "speed", "spell", "spend", "spice", "spine", "spoke", "spoon",
    "spray", "squad", "stack", "staff", "stage", "stain", "stair",
    "stake", "stale", "stall", "stamp", "stand", "stark", "start",
    "state", "stays", "steak", "steam", "steel", "steep", "steer",
    "stern", "stick", "stiff", "still", "stock", "stole", "stone",
    "stood", "stool", "store", "storm", "story", "stove", "strap",
    "straw", "stray", "strip", "stuck", "study", "stuff", "style",
    "sugar", "suite", "sunny", "super", "surge", "swamp", "swear",
    "sweep", "sweet", "swept", "swift", "swing", "sword", "syrup",
    "table", "taste", "teach", "tempo", "thick", "thing", "think",
    "third", "thorn", "those", "three", "threw", "throw", "thumb",
    "tiger", "tight", "timer", "title", "token", "topic", "torch",
    "total", "touch", "tough", "tower", "toxic", "trace", "track",
    "trade", "trail", "train", "trait", "trend", "trial", "tribe",
    "trick", "tried", "troop", "trout", "truck", "truly", "trunk",
    "trust", "truth", "tulip", "tumor", "twice", "twist", "ultra",
    "under", "unify", "union", "unite", "unity", "until", "upper",
    "upset", "urban", "usage", "usual", "utter", "valid", "value",
    "vapor", "vault", "verse", "vigor", "viral", "visit", "vital",
    "vivid", "vocal", "voice", "voter", "waist", "waste", "watch",
    "water", "weary", "weave", "wheel", "where", "which", "while",
    "white", "whole", "whose", "widen", "witch", "woman", "world",
    "worry", "worse", "worst", "worth", "would", "wound", "wrist",
    "wrote", "yield", "young", "youth",
]


@dataclass
class CustodyEntry:
    entry_id: str
    file_dir: Path
    original_filename: str
    is_directory: bool
    expires_at: float
    max_downloads: Optional[int] = None
    download_count: int = 0


class FileCustodyManager:

    def __init__(self):
        config = magnus_config["server"]["file_custody"]
        self._max_size: int = _parse_size_string(config["max_size"])
        raw_file_size = config["max_file_size"]
        self._max_file_size: Optional[int] = _parse_size_string(raw_file_size) if raw_file_size is not None else None
        self._max_processes: int = config["max_processes"]
        self._default_ttl_minutes: int = config["default_ttl_minutes"]
        self._max_ttl_minutes: int = config["max_ttl_minutes"]

        self._storage_root = Path(magnus_config["server"]["root"]) / "file_custody"
        self._storage_root.mkdir(parents=True, exist_ok=True)

        for child in self._storage_root.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
                logger.info(f"Cleaned up stale custody dir: {child.name}")

        self._entries: Dict[str, CustodyEntry] = {}
        self._lock = threading.Lock()
        self._rng = random.SystemRandom()

    def _generate_token(self) -> str:
        for _ in range(64):
            prime = self._rng.choice(_PRIMES)
            words = self._rng.sample(_WORDS, 3)
            token = f"{prime}-{words[0]}-{words[1]}-{words[2]}"
            if token not in self._entries:
                return token
        raise RuntimeError("Failed to generate unique token after 64 attempts")

    def _get_storage_size(self) -> int:
        total = 0
        for dirpath, _, filenames in os.walk(self._storage_root):
            for f in filenames:
                total += os.path.getsize(os.path.join(dirpath, f))
        return total

    def store_file(
        self,
        filename: str,
        file_obj: BinaryIO,
        expire_minutes: Optional[int] = None,
        is_directory: bool = False,
        max_downloads: Optional[int] = None,
    ) -> str:
        if expire_minutes is None:
            expire_minutes = self._default_ttl_minutes
        expire_minutes = min(expire_minutes, self._max_ttl_minutes)

        # 先占位再写文件，避免并发请求绕过 _max_processes 限制
        with self._lock:
            if len(self._entries) >= self._max_processes:
                raise RuntimeError(
                    f"File custody limit reached ({self._max_processes}). "
                    "Try again later or increase max_processes."
                )
            entry_id = self._generate_token()
            placeholder = CustodyEntry(
                entry_id=entry_id,
                file_dir=self._storage_root / entry_id,
                original_filename=filename,
                is_directory=is_directory,
                expires_at=0.0,
                max_downloads=max_downloads,
            )
            self._entries[entry_id] = placeholder

        if self._get_storage_size() >= self._max_size:
            with self._lock:
                self._entries.pop(entry_id, None)
            raise RuntimeError(
                "File custody storage full. "
                "Wait for entries to expire or increase max_size."
            )

        file_dir = placeholder.file_dir
        file_dir.mkdir(parents=True, exist_ok=True)

        file_path = file_dir / filename
        try:
            with open(file_path, "wb") as f:
                written = 0
                while True:
                    chunk = file_obj.read(_COPY_CHUNK_SIZE)
                    if not chunk:
                        break
                    written += len(chunk)
                    if self._max_file_size is not None and written > self._max_file_size:
                        raise FileTooLargeError(filename, self._max_file_size)
                    f.write(chunk)

            if self._get_storage_size() > self._max_size:
                raise RuntimeError(
                    "File custody storage exceeded after write. File removed."
                )
        except Exception:
            with self._lock:
                self._entries.pop(entry_id, None)
            shutil.rmtree(file_dir, ignore_errors=True)
            raise

        # 写入成功，更新过期时间使 entry 生效
        placeholder.expires_at = time.time() + expire_minutes * 60

        logger.info(f"File custody stored: {entry_id}, filename={filename}, expire_minutes={expire_minutes}")
        return entry_id

    def get_entry(self, token: str) -> Optional[CustodyEntry]:
        with self._lock:
            entry = self._entries.get(token)
        if entry is None:
            return None
        if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
            return None
        return entry

    def get_file_path(self, token: str) -> Optional[Tuple[Path, str, bool, bool]]:
        with self._lock:
            entry = self._entries.get(token)
            if entry is None:
                return None
            if entry.expires_at == 0.0 or time.time() >= entry.expires_at:
                return None
            if entry.max_downloads is not None and entry.download_count >= entry.max_downloads:
                return None
            file_path = entry.file_dir / entry.original_filename
            if not file_path.exists():
                return None
            entry.download_count += 1
            exhausted = entry.max_downloads is not None and entry.download_count >= entry.max_downloads
        return (file_path, entry.original_filename, entry.is_directory, exhausted)

    def delete_entry(self, token: str) -> None:
        with self._lock:
            entry = self._entries.pop(token, None)
        if entry is not None and entry.file_dir.exists():
            shutil.rmtree(entry.file_dir, ignore_errors=True)
            logger.info(f"File custody purged (download limit): {token}")

    async def cleanup_loop(self) -> None:
        logger.info("File custody cleanup loop started.")
        while True:
            await asyncio.sleep(30)
            now = time.time()
            with self._lock:
                snapshot = list(self._entries.items())
            expired_ids = [
                eid for eid, entry in snapshot
                if (entry.expires_at > 0.0 and now >= entry.expires_at)
                or (entry.max_downloads is not None and entry.download_count >= entry.max_downloads)
            ]
            for eid in expired_ids:
                with self._lock:
                    entry = self._entries.pop(eid, None)
                if entry is None:
                    continue
                if entry.file_dir.exists():
                    await asyncio.to_thread(shutil.rmtree, entry.file_dir, True)
                logger.info(f"File custody expired: {eid}")

    def shutdown(self) -> None:
        with self._lock:
            entries = list(self._entries.values())
            self._entries.clear()
        logger.info(f"Shutting down file custody manager ({len(entries)} entries)...")
        for entry in entries:
            if entry.file_dir.exists():
                shutil.rmtree(entry.file_dir, ignore_errors=True)


file_custody_manager = FileCustodyManager()
