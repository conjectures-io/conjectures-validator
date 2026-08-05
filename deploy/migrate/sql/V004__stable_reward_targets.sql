ALTER TABLE submissions
    ADD COLUMN reward_target_id text;

UPDATE submissions
SET reward_target_id = CASE problem_id
        WHEN 'fc-e923379e-erdos1094-erdos-1094-01b666aa74175f864ea98b577071421a-problem' THEN 'fc-target:Erdos1094.erdos_1094'
        WHEN 'fc-e923379e-erdos11-erdos-11-ef45c19b49e06b8d799e891bbecc78bd-problem' THEN 'fc-target:Erdos11.erdos_11'
        WHEN 'fc-e923379e-erdos1107-erdos-1107-c72e16fe82aa85ac006b1eba5cd1ad3f-problem' THEN 'fc-target:Erdos1107.erdos_1107'
        WHEN 'fc-e923379e-erdos184-erdos-184-c764216d3519f4f368618eb908156d41-problem' THEN 'fc-target:Erdos184.erdos_184'
        WHEN 'fc-e923379e-erdos233-erdos-233-ea125d7bba4f238810a030ca91216967-problem' THEN 'fc-target:Erdos233.erdos_233'
        WHEN 'fc-e923379e-erdos236-erdos-236-a803bfc34440e3776176d5b2508923c9-problem' THEN 'fc-target:Erdos236.erdos_236'
        WHEN 'fc-e923379e-erdos242-erdos-242-939241f3b34c22446920c261f7410f4e-problem' THEN 'fc-target:Erdos242.erdos_242'
        WHEN 'fc-e923379e-erdos243-erdos-243-d1951cb199108aefec3236766b9fa28a-problem' THEN 'fc-target:Erdos243.erdos_243'
        WHEN 'fc-e923379e-erdos274-herzog-schonheim-256974bd7a1e545b734ee6a199582e6f-problem' THEN 'fc-target:Erdos274.herzog_schonheim'
        WHEN 'fc-e923379e-erdos28-erdos-28-a2f028f341752d03f89481b9ef384e9f-problem' THEN 'fc-target:Erdos28.erdos_28'
        WHEN 'fc-e923379e-erdos282-erdos-282-bcb9ad4be562494c8e075ce0f9b36e72-problem' THEN 'fc-target:Erdos282.erdos_282'
        WHEN 'fc-e923379e-erdos340-erdos-340-b695c52ae5157320107e8626daa73103-problem' THEN 'fc-target:Erdos340.erdos_340'
        WHEN 'fc-e923379e-erdos364-erdos-364-0efeb470025a056f57889953efa4ff78-problem' THEN 'fc-target:Erdos364.erdos_364'
        WHEN 'fc-e923379e-erdos371-erdos-371-4a325f182fc63bb8e3dc8e7464c34382-problem' THEN 'fc-target:Erdos371.erdos_371'
        WHEN 'fc-e923379e-erdos373-erdos-373-4a45af22a7c17af177772028d2ff368c-problem' THEN 'fc-target:Erdos373.erdos_373'
        WHEN 'fc-e923379e-erdos41-erdos-41-d167be12aae45174188bb169a7113151-problem' THEN 'fc-target:Erdos41.erdos_41'
        WHEN 'fc-e923379e-erdos535-erdos-535-27232d2b7e4601fb587cacbbf7a487b2-problem' THEN 'fc-target:Erdos535.erdos_535'
        WHEN 'fc-e923379e-erdos617-erdos-617-a34acf2481f541fc79980c8976555f9e-problem' THEN 'fc-target:Erdos617.erdos_617'
        WHEN 'fc-e923379e-erdos624-erdos-624-bf991f47810035c719176f1719ed526f-problem' THEN 'fc-target:Erdos624.erdos_624'
        WHEN 'fc-e923379e-erdos677-erdos-677-ee735c6d99f3d47bc0615f32baa396e0-problem' THEN 'fc-target:Erdos677.erdos_677'
        WHEN 'fc-e923379e-erdos779-erdos-779-1e8bf46a78400f5c0918e6c58e549d84-problem' THEN 'fc-target:Erdos779.erdos_779'
        WHEN 'fc-e923379e-erdos82-erdos-82-edf7b9f6fb36292956ca6ddb7c1ed88a-problem' THEN 'fc-target:Erdos82.erdos_82'
        WHEN 'fc-e923379e-erdos859-erdos-859-7fce521df5393785c8d4e37f84096bda-problem' THEN 'fc-target:Erdos859.erdos_859'
        WHEN 'fc-e923379e-erdos889-erdos-889-0064adeede80f3d6099f2fd75dc4be6b-problem' THEN 'fc-target:Erdos889.erdos_889'
        WHEN 'fc-e923379e-erdos89-erdos-89-1ba5d4e99789c240c22954fbc22238a6-problem' THEN 'fc-target:Erdos89.erdos_89'
        WHEN 'fc-e923379e-erdos912-erdos-912-6fdd9eb75e0cc2dbd75a07f350601439-problem' THEN 'fc-target:Erdos912.erdos_912'
        WHEN 'fc-e923379e-erdos932-erdos-932-d0dd55dcae50cecbbec327958013996f-problem' THEN 'fc-target:Erdos932.erdos_932'
        WHEN 'fc-e923379e-erdos952-erdos-952-7dc4db1998f8a43aa120cb4efd424c63-problem' THEN 'fc-target:Erdos952.erdos_952'
        WHEN 'fc-e923379e-erdos982-erdos-982-40209ba898582c5ead5f44b4d4b11a3e-problem' THEN 'fc-target:Erdos982.erdos_982'
        ELSE problem_id
    END;

ALTER TABLE submissions
    ALTER COLUMN reward_target_id SET NOT NULL,
    ADD CONSTRAINT reward_target_id_nonempty
        CHECK (length(reward_target_id) BETWEEN 1 AND 255);

DROP INDEX submissions_problem_reward_unique;

CREATE UNIQUE INDEX submissions_reward_target_reward_unique
    ON submissions (reward_target_id)
    WHERE reward_status <> 'INELIGIBLE';
