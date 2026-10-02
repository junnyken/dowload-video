export type ClassValue = string | false | null | undefined | ClassValue[]

export function cn(...classes: ClassValue[]): string {
  const out: string[] = []
  const walk = (v: ClassValue) => {
    if (!v) return
    if (Array.isArray(v)) v.forEach(walk)
    else out.push(v)
  }
  classes.forEach(walk)
  return out.join(' ')
}
