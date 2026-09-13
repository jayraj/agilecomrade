import type { Snapshot } from '../api/client'

const DB_NAME = 'agile-comrade-offline'
const STORE = 'snapshots'
const KEY = 'slug'

interface SnapshotRecord {
  slug: string
  data: Snapshot
  savedAt: string
}

const idbOpen = (): Promise<IDBDatabase> =>
  new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new Error('IndexedDB unavailable'))
      return
    }
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: KEY })
        store.createIndex('savedAt', 'savedAt')
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB open failed'))
  })

const idbTx = <T>(
  db: IDBDatabase,
  mode: IDBTransactionMode,
  run: (store: IDBObjectStore) => IDBRequest<T>,
): Promise<T> =>
  new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode)
    const req = run(tx.objectStore(STORE))
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error ?? new Error('IndexedDB request failed'))
  })

/** Persists the latest snapshot for a profile slug so it can be shown offline. */
export const saveOfflineSnapshot = async (slug: string, snapshot: Snapshot): Promise<void> => {
  try {
    const db = await idbOpen()
    const record: SnapshotRecord = { slug, data: snapshot, savedAt: new Date().toISOString() }
    await idbTx(db, 'readwrite', (store) => store.put(record))
    db.close()
  } catch {
    // Offline cache is best-effort; never break the online path.
  }
}

/** Loads the most recently cached snapshot for a profile slug, or null. */
export const loadOfflineSnapshot = async (slug: string): Promise<Snapshot | null> => {
  try {
    const db = await idbOpen()
    const record = await idbTx(db, 'readonly', (store) => store.get(slug) as IDBRequest<SnapshotRecord>)
    db.close()
    return record?.data ?? null
  } catch {
    return null
  }
}

/** Removes a profile's cached snapshot (used when a profile is deleted). */
export const clearOfflineSnapshot = async (slug: string): Promise<void> => {
  try {
    const db = await idbOpen()
    await idbTx(db, 'readwrite', (store) => store.delete(slug) as IDBRequest<undefined>)
    db.close()
  } catch {
    // Best-effort.
  }
}