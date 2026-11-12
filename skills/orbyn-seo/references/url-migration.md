# URL migration

Covers a domain change, a subdomain moved into a folder, slug clean-up on one
domain, and merging two sites. Links to old URLs live for years, so the redirects
are permanent.

## Mistakes that lose rankings

- Redirecting many old URLs to the home page: search engines treat it as a soft 404
  and the old pages' links stop counting.
- Chains (`http` to `https` to new slug to new domain): send every old URL straight
  to its final URL in one hop.
- Launching with the staging `noindex` or `Disallow: /`.
- Blocking the old domain in robots.txt: crawlers then never see its redirects.
- Mapping only URLs in the current sitemap: retired pages with links start to 404.
- Removing the redirects or letting the old domain lapse.

## Steps

1. **Inventory every old URL:** the sitemap, the route tree, analytics and Search
   Console exports the person provides, and old URLs that still have links.
2. **Map 1-to-1** to each page's closest equivalent. A page with no equivalent goes
   to its nearest parent topic, or returns 410 when it is truly gone.
3. **Implement in the host's own config** (framework redirects, server or edge
   rules) as 301 or 308. Avoid 302 and 307 for permanent moves.
4. **Check the map offline:**
   `python3 scripts/verify_redirects.py --map redirects.csv --old-pages old-urls.txt`
   reports duplicates, self-redirects, chains inside the map (with the direct
   target), loops and old pages with no rule.
5. **Cut over:** update the base URL used for canonicals, social cards and the
   sitemap; the new sitemap lists only new URLs; keep the old sitemap reachable for
   a while so crawlers revisit and see the redirects; update `hreflang`
   alternates; add the old name to the organisation's structured data
   (`alternateName`) and an "(formerly …)" line on the about page; for a full domain
   change, the person runs the search console's change-of-address tool.
6. **Check live** against the person's domain after deploy:
   `python3 scripts/verify_redirects.py --map redirects.csv --base https://old.example.com --allow-host new.example.com`.
   It follows each redirect by hand, only to the listed hosts, and fails on a 302
   or 307, more than one hop, a wrong target, a loop or a target that is not 200.
   A target on another host is checked only when that host is passed with
   `--allow-host`; otherwise the rule fails as not requested. `--slash loose`
   (default) treats `/a` and `/a/` as the same; use `strict` when the site does not.
   Exit 0 means every rule passed, 1 any failure (offline issues included), 2 bad
   input.
7. **Watch** the new property's indexed count rise as the old one falls; a flat count
   after about two weeks points to a crawl blocker.

Asking site owners to update their links is outreach: only on the person's request,
and they send it.
