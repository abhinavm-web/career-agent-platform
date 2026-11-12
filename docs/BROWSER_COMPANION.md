# Browser job capture

The optional Chromium companion copies the current page's title, URL and text you
explicitly select. It does not browse in the background, contact a server, fill
forms or submit applications. The clipboard is accessible to other local apps;
page URLs and selected text may contain private information. Review and remove
anything sensitive before saving.

On the same click, the companion also inspects bounded JSON-LD on the active
page. If it finds exactly one supported [Schema.org JobPosting](https://schema.org/JobPosting), it may include that
posting's title and hiring-organization name as unverified hints. It does not
copy other structured fields; ambiguous or oversized metadata is ignored. The
page may be inaccurate, stale, or describe a different entity, so review the
suggested title and company. An existing company field is never overwritten.

In Chrome or Edge, open the extensions page, enable developer mode and choose
**Load unpacked**, then select this repository's `browser-companion` directory.
On a job page, select the description, open the companion and choose **Copy
selected job**. In the local dashboard choose **Add opportunity → Paste a browser
capture**, paste, and choose **Fill job fields**. Review the title, URL and text,
enter or correct the company and any location, and explicitly save. The page title is not
necessarily the actual job title. The companion cannot access browser-internal
pages, and long selections must be shortened. No listing is fetched or verified;
confirm it with the employer before applying. Existing job deduplication uses the
URL and does not automatically merge possible duplicates.

The extension requests `activeTab`, `scripting`, and `clipboardWrite`; access to
the active page is temporary and only used on an explicit button click. It does
not request broad website permissions or persist captured text in extension
storage. See [Chrome's activeTab documentation](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab).
