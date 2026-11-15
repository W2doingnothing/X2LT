const palettes: Record<string, string[]> = {
  gold: ["#e6cfad", "#b8946e", "#716d57", "#343f36"],
  night: ["#323b65", "#877193", "#394760", "#151f34"],
  sea: ["#b6d5d8", "#8eb0b5", "#597b87", "#2a4856"],
  sand: ["#e7ddce", "#ccbba3", "#ac9e88", "#736f61"],
};
export function art(color: string, n = 0) {
  const c = palettes[color] || palettes.gold;
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 640 400"><defs><linearGradient id="s" x2="0" y2="1"><stop stop-color="${c[0]}"/><stop offset="1" stop-color="${c[1]}"/></linearGradient><linearGradient id="l" x2="0" y2="1"><stop stop-color="${c[1]}"/><stop offset="1" stop-color="${c[3]}"/></linearGradient><filter id="noise"><feTurbulence baseFrequency=".7" numOctaves="3" stitchTiles="stitch"/><feColorMatrix type="saturate" values="0"/><feComponentTransfer><feFuncA type="linear" slope=".08"/></feComponentTransfer><feBlend in="SourceGraphic" mode="soft-light"/></filter></defs><g filter="url(#noise)"><path fill="url(#s)" d="M0 0h640v400H0z"/><circle cx="${460 - n * 65}" cy="${95 + n * 15}" r="${32 + n * 5}" fill="#fff2d8" opacity=".7"/><path d="M-40 270L${110 + n * 50} 110 310 269 450 ${150 - n * 12} 700 290V400H0Z" fill="${c[2]}"/><path d="M-30 310L150 235 230 280 380 180 700 350V400H0Z" fill="${c[3]}" opacity=".78"/><path d="M0 300Q200 280 350 320T640 300V400H0Z" fill="url(#l)"/><path d="M180 342h280M220 356h170M100 374h280" stroke="${c[0]}" opacity=".28" stroke-width="2"/><path d="M0 400V275l25-60 20 60 16-45 20 70 36 100Z" fill="${c[3]}"/></g></svg>`;
  return "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
}
