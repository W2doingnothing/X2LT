# SPDX-License-Identifier: GPL-2.0-only
"""Small shared emoji grammar; no text, multiple symbols or control characters."""
def validate_emoji(value):
    if not isinstance(value,str):raise ValueError('请选择一个 emoji 标识')
    value=value.strip()
    def base(c):
        n=ord(c)
        return (0x1f000<=n<=0x1faff and not 0x1f1e6<=n<=0x1f1ff and not 0x1f3fb<=n<=0x1f3ff) or 0x2600<=n<=0x27ff or 0x2194<=n<=0x23ff or 0x2b00<=n<=0x2bff or n in (0xa9,0xae,0x203c,0x2049,0x3030,0x303d,0x3297,0x3299)
    def part(s):
        if len(s)>=2 and s[0] in '0123456789#*' and s[-1]=='\u20e3':return s[1:-1] in ('','\ufe0f')
        if len(s)==2 and all(0x1f1e6<=ord(c)<=0x1f1ff for c in s):return True
        if not s or not base(s[0]):return False
        rest=s[1:]
        if rest.startswith(('\ufe0f','\ufe0e')):rest=rest[1:]
        if rest and 0x1f3fb<=ord(rest[0])<=0x1f3ff:rest=rest[1:]
        return not rest
    if not value or len(value)>32 or not all(part(p) for p in value.split('\u200d')):
        raise ValueError('请选择一个 emoji 标识')
    return value
