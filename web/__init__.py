"""
Theta's window on the live web.

Two modules, imported explicitly:

    from web.search import search, SearchError, SearchResult
    from web.fetch  import fetch, fetch_many, Page, FetchError

Nothing is re-exported here on purpose: `web.search` and `web.fetch` are both
module names *and* the natural function names, and re-exporting the functions at
package level would shadow the modules.
"""
