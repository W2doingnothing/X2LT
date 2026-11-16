export function validEmoji(value: string): boolean {
  const chars = [...value.trim()];
  const regional = (n: number) => n >= 0x1f1e6 && n <= 0x1f1ff;
  const skin = (n: number) => n >= 0x1f3fb && n <= 0x1f3ff;
  const base = (n: number) =>
    (n >= 0x1f000 && n <= 0x1faff && !regional(n) && !skin(n)) ||
    (n >= 0x2600 && n <= 0x27ff) ||
    (n >= 0x2194 && n <= 0x23ff) ||
    (n >= 0x2b00 && n <= 0x2bff) ||
    [0xa9, 0xae, 0x203c, 0x2049, 0x3030, 0x303d, 0x3297, 0x3299].includes(n);
  return (
    !!chars.length &&
    chars.length <= 32 &&
    value
      .trim()
      .split("\u200d")
      .every((part) => {
        const p = [...part];
        if (
          p.length >= 2 &&
          "0123456789#*".includes(p[0]) &&
          p.at(-1) === "\u20e3"
        )
          return ["", "\ufe0f"].includes(p.slice(1, -1).join(""));
        if (p.length === 2 && p.every((c) => regional(c.codePointAt(0)!)))
          return true;
        if (!p.length || !base(p.shift()!.codePointAt(0)!)) return false;
        if (["\ufe0f", "\ufe0e"].includes(p[0])) p.shift();
        if (p.length && skin(p[0].codePointAt(0)!)) p.shift();
        return !p.length;
      })
  );
}
export function EmojiPicker({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <section className="emoji-picker" aria-label="媒体库标识">
      <label>
        Emoji 标识
        <input
          aria-label="媒体库 emoji"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        />
      </label>
      <div className="emoji-presets">
        {[
          "📁",
          "🎬",
          "🖼️",
          "📷",
          "🎵",
          "📚",
          "⭐",
          "💎",
          "🌸",
          "🎮",
          "🐱",
          "🌍",
          "💼",
          "🎨",
          "🏠",
          "❤️",
        ].map((emoji) => (
          <button
            type="button"
            key={emoji}
            aria-label={"选择标识 " + emoji}
            aria-pressed={value === emoji}
            onClick={() => onChange(emoji)}
          >
            {emoji}
          </button>
        ))}
      </div>
      {!validEmoji(value) && (
        <span className="error" role="alert">
          请选择一个 emoji 标识
        </span>
      )}
    </section>
  );
}
