export interface ProfileCred {
  slug: string
  token: string
  label?: string
}

const PROFILES_KEY = 'srr2_profiles'
const ACTIVE_KEY = 'srr2_active_profile'

export const profileApi = {
  list(): ProfileCred[] {
    try {
      const raw = localStorage.getItem(PROFILES_KEY)
      const parsed = raw ? JSON.parse(raw) : []
      return Array.isArray(parsed) ? parsed : []
    } catch {
      return []
    }
  },

  save(list: ProfileCred[]) {
    localStorage.setItem(PROFILES_KEY, JSON.stringify(list))
  },

  activeSlug(): string | null {
    return localStorage.getItem(ACTIVE_KEY)
  },

  setActiveSlug(slug: string | null) {
    if (slug) {
      localStorage.setItem(ACTIVE_KEY, slug)
    } else {
      localStorage.removeItem(ACTIVE_KEY)
    }
  },

  active(): ProfileCred | null {
    const slug = this.activeSlug()
    if (!slug) return null
    return this.list().find((p) => p.slug === slug) || null
  },

  add(cred: ProfileCred) {
    const list = this.list().filter((p) => p.slug !== cred.slug)
    list.push(cred)
    this.save(list)
  },

  remove(slug: string) {
    this.save(this.list().filter((p) => p.slug !== slug))
    if (this.activeSlug() === slug) {
      this.setActiveSlug(null)
    }
  },

  generateToken(): string {
    const c: Crypto | undefined = globalThis.crypto
    if (c && typeof c.randomUUID === 'function') {
      return c.randomUUID()
    }
    if (c) {
      const bytes = new Uint8Array(16)
      c.getRandomValues(bytes)
      return 'srr-' + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
    }
    throw new Error('Secure random number generation is not available in this browser.')
  },
}