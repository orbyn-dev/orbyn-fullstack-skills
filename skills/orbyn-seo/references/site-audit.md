# Site audit

Check in this order and fix each fatal item before moving on: a page search engines
cannot crawl gains nothing from better copy. Check a local or preview build first,
then the person's own live domain with plain requests. Record each check as pass,
fail or not checked, with the command or page that shows it.

## 1. Crawl and index blockers (fatal)

- **robots.txt allows by default.** `User-agent: *` must not be `Disallow: /`.
  Disallow only private paths that exist (an API, the signed-in app, admin). Never
  block CSS, JavaScript or framework asset paths: search engines render the page
  with them. List the sitemap with an absolute `Sitemap:` line.
- **A sitemap exists** and lists only canonical, indexable URLs that return 200.
- **Unknown paths return 404.** Request a made-up path; a 200 means a catch-all
  route renders every typo as a page. Restrict the dynamic segment to known values
  and return not-found for the rest.
- **No leaked `noindex`.** Check the robots meta tag and the `X-Robots-Tag` header on
  production. Previews and staging stay `noindex`; production must not inherit it.
- **To remove indexed URLs, `noindex` first, then disallow.** A disallowed URL cannot
  be recrawled, so its `noindex` is never seen and it stays indexed.

## 2. Locales

Keep only locales that are fully translated and that people use. Half-translated
locale trees are near-duplicates. Each kept page lists every locale version,
including itself, plus `x-default`, and each version links back. Drop the rest with
`noindex` and remove them from the sitemap, or redirect them to the default page.
Locale tags follow [page metadata](../../orbyn-ui-design/references/page-metadata.md).

## 3. Content crawlers can read

Many crawlers, including most AI crawlers, do not run JavaScript. Fetch the raw
HTML and check that prices, plans, limits, product facts and main text are there as
text, not only rendered by a client script or hidden behind a login. Icon-only facts
(a bed icon and "2") are invisible to them; write "2 bedrooms". A crawler that gets
a challenge page or 403 from a firewall sees nothing; check bot rules with the
person before changing them.

## 4. Generated and template pages

- Pages with no or almost no results get `noindex, follow` and leave the sitemap.
- Slugs come from names, not IDs; redirect old ID URLs once.
- Search, sort and filter parameter URLs stay out of the index.
- One fact, one source: a count or price shown in the heading, an FAQ, structured
  data and `llms.txt` comes from the same constant, derived from the data.
- A repeated FAQ answer on many pages is boilerplate: make it page-specific or drop it.

## 5. Money pages

For each money page: in the sitemap, canonical to itself, linked from the home page
or main navigation, at least three contextual links from body text (navigation and
footer excluded), no redirect chain, and the first screen shows the product's value
rather than only a "book a demo" wall. Check indexation in Search Console when the
person has it; otherwise mark it not checked.

## Report

List fatal, revenue and polish items in that order, each with its evidence and fix.
Polish (word counts, minor title tweaks) waits until fatal items are fixed.
