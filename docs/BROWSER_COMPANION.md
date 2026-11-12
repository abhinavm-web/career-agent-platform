# Browser job capture

The optional Chromium companion copies the current page's title, URL and either text you
explicitly select or a structured job description you explicitly request. It does not browse in the background, contact a server, fill
forms or submit applications. The clipboard is accessible to other local apps;
page URLs and selected text may contain private information. Review and remove
anything sensitive before saving.

On either capture click, the companion also inspects bounded JSON-LD on the active
page. If it finds exactly one supported [Schema.org JobPosting](https://schema.org/JobPosting), it may include that
posting's title and hiring-organization name as unverified hints. **Copy structured description** also copies the description from that single posting, converting supported HTML to plain text without running scripts. HTML list items become `-` bullets and formatting is simplified, so compare the result with the page. It does not copy other structured fields; ambiguous or oversized metadata is ignored. The
page may be inaccurate, stale, or describe a different entity, so review the
suggested title and company. An existing company field is never overwritten.

In Chrome or Edge, open the extensions page, enable developer mode and choose
**Load unpacked**, then select this repository's `browser-companion` directory.
On a job page, select the description, open the companion and choose **Copy
selected job**, or choose **Copy structured description** when the page contains one usable JobPosting description. If the extension is already installed, reload it from the extensions page after updating. Structured data may be missing, stale, incomplete, or for a different role; no selection-free capture is available when there are multiple postings. In the local dashboard choose **Add opportunity → Paste a browser
capture**, paste, and choose **Fill job fields**. Review the title, URL and text,
enter or correct the company and any location, and explicitly save. The page title is not
necessarily the actual job title. The companion cannot access browser-internal
pages, and long selections must be shortened. No listing is fetched or verified;
confirm it with the employer before applying. Existing job deduplication uses the
URL and does not automatically merge possible duplicates.

For an existing saved job, open **Correct saved description → Use a browser capture for this job**, paste the capture and choose **Fill description from capture**. The app checks the URL against the saved job locally, ignoring the same tracking parameters used in job deduplication, then fills the editor without saving. Structured captures also prefill an unverified source note if that note is empty. Review the text and source, explicitly confirm and save the correction; existing draft and approval safeguards still apply. Captures with a URL fragment, an ambiguous `ref` query parameter or a different URL are not matched automatically; if appropriate, inspect the original posting and paste the text into the editor manually. Matching a URL does not verify the posting's accuracy or current availability.

The extension requests `activeTab`, `scripting`, and `clipboardWrite`; access to
the active page is temporary and only used on an explicit button click. It does
not request broad website permissions or persist captured text in extension
storage. See [Chrome's activeTab documentation](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab).
