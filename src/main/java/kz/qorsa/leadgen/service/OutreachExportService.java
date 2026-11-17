package kz.qorsa.leadgen.service;

import java.util.List;
import java.util.Optional;
import java.util.UUID;
import kz.qorsa.leadgen.domain.Lead;
import kz.qorsa.leadgen.domain.Outreach;
import kz.qorsa.leadgen.domain.OutreachStatus;
import kz.qorsa.leadgen.repository.LeadRepository;
import kz.qorsa.leadgen.repository.OutreachRepository;
import lombok.extern.slf4j.Slf4j;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

/**
 * Hands HOT leads to an outbound channel (currently workers/amocrm) and
 * records what was pushed, so nothing is pushed twice.
 *
 * <p>The record lives in the {@code outreach} table, which was modelled for
 * exactly this and has been empty until now. One row per (lead, channel):
 * its {@code message} holds the CRM's own id for the deal, which is what
 * makes the link traceable in both directions.
 *
 * <p>Two decisions worth keeping:
 * <ul>
 *   <li><b>HOT only.</b> A one-seat CRM with 500 QUALIFIED rows in it is a
 *   CRM nobody opens. Leads that climb to HOT later are picked up on a later
 *   run - the query has no date window.</li>
 *   <li><b>The mark is written by the core, not the worker.</b> The worker
 *   reports the CRM id back and the core decides it's done, so a worker that
 *   dies mid-run leaves the lead pending rather than silently skipped.</li>
 * </ul>
 */
@Service
@Slf4j
public class OutreachExportService {

    /** Channel name stored in outreach.channel. */
    public static final String CHANNEL_AMOCRM = "amocrm";

    static final int MAX_PENDING_LIMIT = 200;

    private final LeadRepository leadRepository;
    private final OutreachRepository outreachRepository;

    public OutreachExportService(LeadRepository leadRepository, OutreachRepository outreachRepository) {
        this.leadRepository = leadRepository;
        this.outreachRepository = outreachRepository;
    }

    public List<Lead> findPending(String channel, int limit) {
        int clamped = Math.max(1, Math.min(limit, MAX_PENDING_LIMIT));
        return leadRepository.findHotNotPushedTo(channel, PageRequest.of(0, clamped));
    }

    /**
     * Records that a lead reached the channel. Idempotent: a repeat call for
     * the same (lead, channel) is ignored rather than creating a second row,
     * because a retry after a network timeout is the normal case - the deal
     * may well have been created before the response was lost.
     */
    @Transactional
    public Optional<Outreach> markPushed(UUID leadId, String channel, String externalId) {
        Optional<Lead> found = leadRepository.findById(leadId);
        if (found.isEmpty()) {
            return Optional.empty();
        }
        if (outreachRepository.existsByLeadIdAndChannel(leadId, channel)) {
            log.debug("Lead {} already pushed to {}, ignoring", leadId, channel);
            return outreachRepository.findAll().stream()
                    .filter(o -> o.getLead().getId().equals(leadId) && channel.equals(o.getChannel()))
                    .findFirst();
        }

        Outreach outreach = Outreach.builder()
                .lead(found.get())
                .channel(channel)
                .message(externalId)
                .status(OutreachStatus.SENT)
                .build();

        Outreach saved = outreachRepository.save(outreach);
        log.info("Lead {} pushed to {} as {}", leadId, channel, externalId);
        return Optional.of(saved);
    }
}
