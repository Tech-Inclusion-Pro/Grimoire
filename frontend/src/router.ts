/** Minimal URL routing.
 *
 * The server already serves index.html for any non-API path, so a deep link
 * or a refresh lands back in the app. This makes the app actually read that
 * URL, so /decks/12 opens deck 12 instead of the deck list -- and so the
 * back button behaves the way anyone would expect on an iPad.
 */
import { useEffect, useState } from 'react'

export type Route =
  | { name: 'decks' }
  | { name: 'deck'; id: number }
  | { name: 'playtest'; id: number }
  | { name: 'newDeck' }
  | { name: 'collection' }
  | { name: 'synergy' }
  | { name: 'shelf' }

export function parse(pathname: string): Route {
  const parts = pathname.replace(/^\/+|\/+$/g, '').split('/')
  if (parts[0] === 'decks') {
    if (parts[1] === 'new') return { name: 'newDeck' }
    const id = Number(parts[1])
    if (Number.isInteger(id) && id > 0) {
      return parts[2] === 'playtest' ? { name: 'playtest', id } : { name: 'deck', id }
    }
    return { name: 'decks' }
  }
  if (parts[0] === 'collection') return { name: 'collection' }
  if (parts[0] === 'synergy') return { name: 'synergy' }
  if (parts[0] === 'shelf') return { name: 'shelf' }
  return { name: 'decks' }
}

export function href(route: Route): string {
  switch (route.name) {
    case 'deck': return `/decks/${route.id}`
    case 'playtest': return `/decks/${route.id}/playtest`
    case 'newDeck': return '/decks/new'
    case 'decks': return '/decks'
    default: return `/${route.name}`
  }
}

export function navigate(route: Route) {
  history.pushState(null, '', href(route))
  dispatchEvent(new PopStateEvent('popstate'))
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parse(location.pathname))
  useEffect(() => {
    const onPop = () => setRoute(parse(location.pathname))
    addEventListener('popstate', onPop)
    return () => removeEventListener('popstate', onPop)
  }, [])
  return route
}
