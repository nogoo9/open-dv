/**
 * Clarification Question & Answer Payload Normalization Helpers
 * (apps/opendv-web-workbench/src/ontology/store/clarification-normalizer.ts)
 */

import { EMPTY_ANSWERS_SHA256 } from "./scorecard";
import { computeJcsSha256Hex } from "./studio-fetch";
import type {
	ClarificationAnswer,
	ClarificationEvidenceCitation,
	ClarificationQuestion,
	ClarificationRationaleOption,
} from "./types";

export async function normalizeClarificationsResponse(
	data:
		| Array<Record<string, unknown>>
		| {
				questions?: Array<Record<string, unknown>>;
				answers?: Record<string, unknown> | Array<Record<string, unknown>>;
				answers_sha256?: string;
		  },
	rawAnswersPayload: unknown,
	questionsEtag?: string,
	answersEtag?: string,
): Promise<{
	normalizedQuestions: ClarificationQuestion[];
	answersMap: Record<string, ClarificationAnswer>;
	answersSha?: string | undefined;
}> {
	const rawQuestions: Array<Record<string, unknown>> = Array.isArray(data)
		? data
		: data.questions || [];

	const answersListFromEndpoint: Array<Record<string, unknown>> | null =
		Array.isArray(rawAnswersPayload)
			? (rawAnswersPayload as Array<Record<string, unknown>>)
			: rawAnswersPayload &&
					typeof rawAnswersPayload === "object" &&
					Array.isArray((rawAnswersPayload as { answers?: unknown }).answers)
				? ((
						rawAnswersPayload as {
							answers: Array<Record<string, unknown>>;
						}
					).answers ?? null)
				: null;

	const answersMap: Record<string, ClarificationAnswer> = {};
	if (answersListFromEndpoint) {
		for (const item of answersListFromEndpoint) {
			const qid = String(item.question_id || "");
			if (!qid) continue;
			const sel = Array.isArray(item.selected_options)
				? (item.selected_options as string[])
				: Array.isArray(item.selected_option_ids)
					? (item.selected_option_ids as string[])
					: [];
			const uText =
				typeof item.user_text === "string"
					? item.user_text
					: typeof item.custom_answer_text === "string"
						? item.custom_answer_text
						: undefined;
			answersMap[qid] = {
				question_id: qid,
				selected_option_ids: sel,
				custom_answer_text: uText,
				answered_at:
					typeof item.answered_at === "string" ? item.answered_at : undefined,
			};
		}
	}

	const normalizedQuestions: ClarificationQuestion[] = rawQuestions.map(
		(rq) => {
			const qid = String(rq.question_id || "");
			const rawOpts = Array.isArray(rq.rationale_options)
				? (rq.rationale_options as Array<Record<string, unknown>>)
				: Array.isArray(rq.options)
					? (rq.options as Array<Record<string, unknown>>)
					: [];
			const rationaleOptions: ClarificationRationaleOption[] = rawOpts.map(
				(opt, idx) => ({
					id: String(opt.id || opt.option_id || `opt-${idx + 1}`),
					label: String(opt.label || opt.option_id || `Option ${idx + 1}`),
					description:
						typeof opt.description === "string"
							? opt.description
							: typeof opt.rationale === "string"
								? opt.rationale
								: undefined,
					fibo_uri: typeof opt.fibo_uri === "string" ? opt.fibo_uri : undefined,
					evidence_citations: Array.isArray(opt.evidence_citations)
						? (opt.evidence_citations as Array<Record<string, unknown>>)
						: undefined,
				}),
			);

			const rawCites = Array.isArray(rq.evidence_citations)
				? (rq.evidence_citations as Array<Record<string, unknown>>)
				: [];
			const citations: ClarificationEvidenceCitation[] = rawCites.map((c) => ({
				source_file: String(
					c.source_file ||
						c.reference_uri ||
						c.source_id ||
						c.source_label ||
						"source",
				),
				chunk_id: String(c.chunk_id || "chunk"),
				excerpt: String(c.excerpt || c.snippet || c.text_snippet || ""),
				evidence_type:
					typeof c.evidence_type === "string" ? c.evidence_type : undefined,
				source_label:
					typeof c.source_label === "string" ? c.source_label : undefined,
			}));

			const matchedAnswer = answersMap[qid];
			const legacySelected = Array.isArray(rq.selected_option_ids)
				? (rq.selected_option_ids as string[])
				: Array.isArray(rq.selected_options)
					? (rq.selected_options as string[])
					: undefined;
			const legacyStatus = String(rq.status || "").toUpperCase();
			const isAnswered = Boolean(matchedAnswer) || legacyStatus === "ANSWERED";

			if (
				!matchedAnswer &&
				isAnswered &&
				legacySelected &&
				legacySelected.length > 0
			) {
				answersMap[qid] = {
					question_id: qid,
					selected_option_ids: legacySelected,
					custom_answer_text:
						typeof rq.custom_answer_text === "string"
							? rq.custom_answer_text
							: undefined,
					answered_at:
						typeof rq.answered_at === "string" ? rq.answered_at : undefined,
				};
			}

			const rawCertainty = String(rq.certainty || "MEDIUM").toUpperCase();
			const certainty: "HIGH" | "MEDIUM" | "LOW" =
				rawCertainty === "HIGH" ||
				rawCertainty === "MEDIUM" ||
				rawCertainty === "LOW"
					? rawCertainty
					: "MEDIUM";

			return {
				question_id: qid,
				concept_name: String(rq.concept_name || rq.focus_concept || "Concept"),
				focus_concept:
					typeof rq.focus_concept === "string" ? rq.focus_concept : undefined,
				question_text: String(rq.question_text || ""),
				certainty,
				rationale_options: rationaleOptions,
				evidence_citations: citations,
				status: isAnswered ? "ANSWERED" : "PENDING",
				answered_at:
					matchedAnswer?.answered_at ??
					(typeof rq.answered_at === "string" ? rq.answered_at : undefined),
				selected_option_ids:
					matchedAnswer?.selected_option_ids ?? legacySelected,
				custom_answer_text:
					matchedAnswer?.custom_answer_text ??
					(typeof rq.custom_answer_text === "string"
						? rq.custom_answer_text
						: undefined),
			};
		},
	);

	let answersSha: string | undefined =
		(rawAnswersPayload &&
		typeof rawAnswersPayload === "object" &&
		!Array.isArray(rawAnswersPayload)
			? (rawAnswersPayload as { answers_sha256?: string }).answers_sha256
			: undefined) ||
		answersEtag ||
		(!Array.isArray(data) ? data.answers_sha256 : undefined) ||
		questionsEtag ||
		undefined;

	if (!answersSha && answersListFromEndpoint !== null) {
		if (answersListFromEndpoint.length === 0) {
			answersSha = EMPTY_ANSWERS_SHA256;
		} else {
			const sortedRawAnswers = [...answersListFromEndpoint].sort((a, b) => {
				const qa = String(a.question_id || "");
				const qb = String(b.question_id || "");
				return qa < qb ? -1 : qa > qb ? 1 : 0;
			});
			answersSha =
				(await computeJcsSha256Hex(sortedRawAnswers)) ?? EMPTY_ANSWERS_SHA256;
		}
	}

	return { normalizedQuestions, answersMap, answersSha };
}
