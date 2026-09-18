DROP TABLE IF EXISTS job, llm_call, seasonal_produce, setting, profile_suggestion, metric,
  activity, workout_log, rejection, period_feedback, meal_review, meal_feedback, grocery_item,
  plan_meal, recipe, training_summary, plan, llm_task_config, prompt_version, training_availability, shopping_trip,
  period_day, period, equipment_profile, goal, app_user, member CASCADE;

DROP TYPE IF EXISTS llm_call_status, job_status, suggestion_status, metric_kind, session_status,
  repeat_signal, ate_signal, portion_signal, recipe_verdict, plan_status, plan_kind, meal_slot,
  cook_time, lunch_mode, dinner_mode, period_status, goal_kind, member_kind;

DROP FUNCTION IF EXISTS app_is_worker();
DROP FUNCTION IF EXISTS app_member();
DROP FUNCTION IF EXISTS app_household();
