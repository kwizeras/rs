from typing import Union
from rsptx.db.models import AuthUserValidator
from rsptx.db.crud import (
    is_assigned,
    fetch_answers,
    create_question_grade_entry,
    fetch_grade,
    fetch_question_grade,
    update_question_grade_entry,
    upsert_grade,
    fetch_assignment_scores,
    did_send_messages,
)
from rsptx.validation.schemas import LogItemIncoming, ScoringSpecification
from rsptx.db.models import GradeValidator
from rsptx.logging import rslogger


async def grade_submission(
    user: AuthUserValidator, submission: LogItemIncoming
) -> None:
    """
    Grade a submission and store the results in the database.

    :param user: The user who submitted the answer.
    :type user: AuthUserValidator
    :param submission: The submission to grade.
    :type submission: LogItemIncoming
    """

    # First figure out if the answer is part of an assignment
    update_total = False
    scoreSpec = await is_assigned(
        submission.div_id, user.course_id, submission.assignment_id
    )
    if scoreSpec.assigned:
        rslogger.debug(
            f"Scoring {submission.div_id} for {user.username} scoreSpec = {scoreSpec}"
        )
        if scoreSpec.which_to_grade == "first_answer":
            answers = await fetch_answers(
                submission.div_id, submission.event, user.course_name, user.username
            )
            if len(answers) > 0:
                return
            else:
                scoreSpec.score = await score_one_answer(scoreSpec, submission)
                await create_question_grade_entry(
                    user.username, user.course_name, submission.div_id, scoreSpec.score
                )
                update_total = True
        elif scoreSpec.which_to_grade == "last_answer":
            scoreSpec.score = await score_one_answer(scoreSpec, submission)
            answer = await fetch_question_grade(
                user.username, user.course_name, submission.div_id
            )
            if answer:
                answer.score = scoreSpec.score
                await update_question_grade_entry(
                    user.username,
                    user.course_name,
                    submission.div_id,
                    scoreSpec.score,
                    answer.id,
                )
            else:
                await create_question_grade_entry(
                    user.username, user.course_name, submission.div_id, scoreSpec.score
                )
            update_total = True
        elif scoreSpec.which_to_grade == "best_answer":
            scoreSpec.score = await score_one_answer(scoreSpec, submission)
            rslogger.debug(
                f"Scoring best answer with current score of {scoreSpec.score}"
            )
            current_score = await fetch_question_grade(
                user.username, user.course_name, submission.div_id
            )
            # Update the score unless the instructor has left a comment.
            # Insructors should have the last word?
            if current_score:
                if (
                    current_score.score < scoreSpec.score
                    and current_score.comment == "autograded"
                ):
                    await update_question_grade_entry(
                        user.username,
                        user.course_name,
                        submission.div_id,
                        scoreSpec.score,
                        current_score.id,
                    )
                    update_total = True
                else:
                    scoreSpec.score = current_score.score
            else:
                await create_question_grade_entry(
                    user.username,
                    user.course_name,
                    submission.div_id,
                    scoreSpec.score,
                )
                update_total = True
        elif scoreSpec.which_to_grade == "all_answer":
            # This is a peer instruction question.
            # check to make sure that this is vote2 before we do anything.
            # PI questions are scored similar to interact.
            if "vote2" in submission.act:
                scoreSpec.username = user.username
                scoreSpec.score = await score_one_answer(scoreSpec, submission)
                answer = await fetch_question_grade(
                    user.username, user.course_name, submission.div_id
                )
                if answer:
                    answer.score = scoreSpec.score
                    await update_question_grade_entry(
                        user.username,
                        user.course_name,
                        submission.div_id,
                        scoreSpec.score,
                        answer.id,
                    )
                else:
                    await create_question_grade_entry(
                        user.username,
                        user.course_name,
                        submission.div_id,
                        scoreSpec.score,
                    )
                update_total = True
        if update_total:
            rslogger.debug("Updating total score")
            # Now compute the total
            total = await compute_total_score(scoreSpec, user)

    return scoreSpec


async def score_one_answer(
    scoreSpec: ScoringSpecification, submission: LogItemIncoming
) -> Union[int, float]:
    """
    Score a single answer based on the scoring specification.

    :param scoreSpec: The scoring specification.
    :type scoreSpec: ScoringSpecification
    :return: The score for the answer.
    :rtype: Union[int, float]
    """
    if scoreSpec.how_to_score == "pct_correct":
        if submission.correct:
            return scoreSpec.max_score
        else:
            return submission.percent * scoreSpec.max_score
    elif scoreSpec.how_to_score == "all_or_nothing":
        if submission.correct:
            return scoreSpec.max_score
        else:
            return 0
    elif scoreSpec.how_to_score == "interact":
        return scoreSpec.max_score
    elif scoreSpec.how_to_score == "peer":
        return scoreSpec.max_score
    elif scoreSpec.how_to_score == "peer_chat":
        did_chat = await did_send_messages(
            scoreSpec.username, submission.div_id, submission.course_name
        )
        if did_chat:
            return scoreSpec.max_score
    else:
        return 0


async def compute_total_score(
    scoreSpec: ScoringSpecification, user: AuthUserValidator
) -> int:
    """
    Compute the total score for an assignment.

    :param scoreSpec: The scoring specification.
    :type scoreSpec: ScoringSpecification
    :param user: The user to compute the score for.
    :type user: AuthUserValidator
    :rtype: int
    """

    res = await fetch_assignment_scores(
        scoreSpec.assignment_id, user.course_name, user.username
    )
    total = 0
    for row in res:
        total += row.score

    # Now update the grade table with the new total

    grade = await fetch_grade(user.id, scoreSpec.assignment_id)
    if grade:
        grade.score = total
        newGrade = grade
    else:
        newGrade = GradeValidator(
            auth_user=user.id,
            course_name=user.course_name,
            assignment=scoreSpec.assignment_id,
            score=total,
            manual_total=False,
        )
    res = await upsert_grade(newGrade)
    return total
