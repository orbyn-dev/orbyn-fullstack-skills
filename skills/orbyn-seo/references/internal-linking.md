# Internal linking

Search engines and readers follow links. A money page that nothing links to, or that
sits four clicks from the home page, ranks below a blog post on its own topic.

## Measure

Build the site, then run the graph on the output:

```sh
python3 scripts/link_graph.py --site dist --base-url https://example.com --money /pricing --money /signup
```

It reads the built HTML only and reports pages, inbound links from distinct pages,
orphans, weak pages, broken internal links, click depth from the home page and, for
each money page, its inbound count, depth and top linking pages. Add `--json` for a
machine-readable report. It never fetches the network or writes files.

How it counts: inbound means distinct linking pages; `nofollow` links are checked
for breakage but add no inbound and no path, so a page reached only through them is
an orphan; weak pages exclude orphans; `/about`, `/about/` and `/about.html` are one
page; without `--base-url`, every absolute link counts as external. It exits 0 after
a successful read and 2 for bad input.

## Fix, in order

1. Broken internal links and orphans.
2. Money pages deeper than about three clicks: link them from the home page or main
   navigation.
3. Contextual links from the pages that already get traffic to the money pages they
   discuss.

## Contextual link rules

- Link only where the page already discusses the target's topic.
- Put at least one new link in the upper part of the body; readers leave as they
  scroll.
- Two to four new money links per page, at most one per target per page.
- Anchors are short descriptive phrases saying what the target does, not "click
  here" and not the same exact phrase on every page.
- Never mark internal links `nofollow`.
- Edit minimally: wrap an existing phrase, or add one useful sentence; do not
  rewrite the paragraph.
- Hubs link to their children and children back to the hub; breadcrumbs match the
  real hierarchy.

Run the graph again after the change and report the before and after numbers.
