package kz.qorsa.leadgen.service;

import java.time.Instant;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Company;
import kz.qorsa.leadgen.repository.CompanyRepository;
import kz.qorsa.leadgen.web.dto.EgrulPatchRequest;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Stores what workers/egrul found in the state company register.
 *
 * <p>Exists to answer one question a lead list can't: who do you ask for.
 * An OSM company comes with a switchboard number and nothing else, and
 * "позовите директора" is where a cold call dies. The register publishes the
 * name and post of whoever runs each legal entity, which turns that same call
 * into asking for a specific person.
 *
 * <p>Scope is deliberately narrow. This stores a name, a post, ИНН and ОГРН -
 * facts the registry publishes about a company's management. It does not
 * store personal contact details, and the worker doesn't fetch any.
 *
 * <p>Unlike enrichment, nothing here feeds scoring. A named CEO doesn't make
 * a company a better prospect; it makes the call easier. So {@link Company}
 * is saved but no rescore is triggered - the score would be identical and the
 * sweep would be wasted work.
 */
@Service
@Slf4j
public class EgrulLookupService {

    public static final String RAW_EGRUL_ATTEMPTED = "egrul_attempted";
    public static final String RAW_EGRUL_AT = "egrul_at";
    public static final String RAW_CEO_NAME = "ceo_name";
    public static final String RAW_CEO_POST = "ceo_post";
    public static final String RAW_LEGAL_NAME = "legal_name";
    public static final String RAW_OGRN = "ogrn";
    public static final String RAW_INN = "inn";
    public static final String RAW_EGRUL_NOTES = "egrul_notes";

    static final int MAX_PENDING_LIMIT = 500;

    private final CompanyRepository companyRepository;

    public EgrulLookupService(CompanyRepository companyRepository) {
        this.companyRepository = companyRepository;
    }

    public List<Company> findPending(int limit) {
        int clamped = Math.max(1, Math.min(limit, MAX_PENDING_LIMIT));
        return companyRepository.findPendingEgrul(clamped);
    }

    /**
     * Applies one lookup. Always marks the company as attempted - including a
     * lookup that matched nothing - so a company with an unmatchable name
     * isn't re-queried on every run, burning the daily quota on a question
     * already answered.
     */
    @Transactional
    public Optional<Company> applyLookup(UUID companyId, EgrulPatchRequest patch) {
        Optional<Company> found = companyRepository.findById(companyId);
        if (found.isEmpty()) {
            return Optional.empty();
        }
        Company company = found.get();
        Map<String, Object> raw = company.getRaw() != null ? new HashMap<>(company.getRaw()) : new HashMap<>();

        putIfPresent(raw, RAW_CEO_NAME, patch.getCeoName());
        putIfPresent(raw, RAW_CEO_POST, patch.getCeoPost());
        putIfPresent(raw, RAW_LEGAL_NAME, patch.getLegalName());
        putIfPresent(raw, RAW_OGRN, patch.getOgrn());
        // ИНН may already be there from a госзакупка notice, which is the more
        // authoritative source for that particular company - don't overwrite.
        if (!raw.containsKey(RAW_INN)) {
            putIfPresent(raw, RAW_INN, patch.getInn());
        }
        putIfPresent(raw, RAW_EGRUL_NOTES, patch.getEgrulNotes());

        raw.put(RAW_EGRUL_ATTEMPTED, true);
        raw.put(RAW_EGRUL_AT, Instant.now().toString());
        company.setRaw(raw);

        Company saved = companyRepository.save(company);
        log.info("EGRUL lookup: company={}, ceo={}, inn={}",
                saved.getId(), patch.getCeoName(), patch.getInn());
        return Optional.of(saved);
    }

    private static void putIfPresent(Map<String, Object> raw, String key, String value) {
        if (value != null && !value.isBlank()) {
            raw.put(key, value.trim());
        }
    }
}
