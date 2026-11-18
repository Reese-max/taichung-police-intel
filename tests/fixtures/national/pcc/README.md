Offline fixtures from main-session GitHub Actions capture, 2026-11-18.

tender.html: pcc-recon-44263950151/police-all-1m.html; three rows, third authority replaced by synthetic out-of-scope value; count reduced to 3; original table header and paging links retained.
award.html: pcc-recon-44262989997/bulletion-police.html; three rows, third authority replaced by synthetic out-of-scope value; count reduced to 3; original table header and paging links retained.
award-old.html: pcc-recon-44263950151/bulletion-114.html; three rows, third authority replaced by synthetic out-of-scope value; count reduced to 3; original table header and paging links retained.
tender.xml: pcc-recon-44263521236/tender_20260902.xml; two scoped rows plus one non-scoped row; private values replaced by PRIVATE_FIXTURE_SENTINEL.
award.xml: pcc-recon-44263521236/award_20260902.xml; first three captured rows, first two authorities replaced by synthetic scoped names (the captured award file has no scoped rows); private values replaced by PRIVATE_FIXTURE_SENTINEL.
award-live.html: nat-w4-live2-fixture/award-live-p1.html; first five of 100 captured rows, unchanged data, including empty tender dates and no-award suffixes; original header and paging links retained, banner count reduced from 2,772 to 5, whitespace trimmed. Tests override the count when replaying pagination.
Round 2: award.html and award-old.html already contain the real ten-column layout. Their two date headings are expanded for clarity; cells[4] remains the tender notice date and cells[5] the award/no-award notice date. Captured date values and suffixes are preserved.
