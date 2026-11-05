"""Predeclared synthetic replay cases; NO candidate execution or real measurements.

Only payloads enter the observer. Rubric/labels stay with the evaluator.
"""
import copy,hashlib,json

def encode(x):return json.dumps(x,ensure_ascii=False)
def sha(x):return hashlib.sha256(x.encode()).hexdigest()

def cases():
    result=[]
    for n in range(1,7):
        jobs=['stream-a','stream-b'] if n==3 else ['stream-a']
        pos={j:0 for j in jobs}
        def rec(kind,obj,data,job='stream-a'):
            pos[job]+=1
            # Mechanical fields deliberately consume the first overview; the
            # decisive details are in the immutable expansion, not a verdict.
            content=encode(dict(provenance=dict(mode='synthetic-replay',real_model=False,real_candidate_execution=False,
                purpose='development',series='lab-records',units='objective-points or operation-counts as explicitly labeled',
                description='These are constructed observation records for an observer replay. Treat their measurements as conditional evidence inside this replay only. They are NOT actual scientific results. Project registry is not a claim about what the human personally knows. No final data is present.'),data=data))
            return dict(job_id=job,batch=job+'/001',session=job+'-author',position=pos[job],kind=kind,object=obj,
                at=1000+pos[job],version=sha(content),content=content,source=dict(synthetic=True,fixture_version=1))
        known=['greedy construction','single-swap local refinement','static graph partition then independent solving']
        if n==5:known+=['cached marginal gains, including lazy refresh and larger candidate pools; effect already reproduced on this same domain and resource range']
        registry=rec('material','registry.json',dict(methods=known,literature_search='not supplied',human_familiarity='unknown',
            objective='maximize solution quality under a fixed objective-evaluation budget',backend='replay-native-function',dataset_role='development'))
        claim={1:'v7 uses boundary-density-dependent joint repair after decomposition; a small quality improvement appeared in the first run.',
            2:'v7 changes the decomposition termination rule. Overall mean quality looks unchanged but larger connected instances now finish.',
            3:'The two teams mention an adaptive boundary-repair mechanism. Their task structures differ. Each initial aggregate gain is small.',
            4:'Unshared analysis: residual error may be concentrated near the separator. If true, solving just the boundary jointly should help high-boundary cases, but not already disconnected graphs. This is a hypothesis; I have no verified result for it yet.',
            5:'We call v7 Aurora Search. The best score increased a lot. It feels like a novel breakthrough.',
            6:'v7 appears to improve objective by 25%. The author calls it an important new method; evaluator comparison details have not been inspected.'}[n]
        first=[registry,rec('analysis','message',dict(text=claim,shared=False,verification='author explanation only'))]
        source=rec('source','candidate-v7.py',dict(kind='source comparison, not executable input',base='fixed_partition(); solve_parts_independently()',
            v7='partition_by_boundary_density(); solve_parts(); repair_shared_boundary_jointly()'))
        meta=dict(status='completed',dataset_purpose='development',valid=True,cleanup_confirmed=True,infrastructure_error=False,
            evaluator_sha='e'*64,backend='replay-native-function',objective_calls_per_case=1000,matched_inputs=True,unique_cases=60)
        if n==1:
            second=[source,rec('evaluation','evaluation-a.json',dict(**meta,dataset_sha='a'*64,paired_repetitions=12,
                base=[100,101,99,100,102,98,100,101,99,102,100,98],v7=[102,103,101,102,104,100,102,103,101,104,102,100],
                v7_without_boundary_repair=[100.1,101.1,99.1,100.1,102.1,98.1,100.1,101.1,99.1,102.1,100.1,98.1],
                tuning='frozen before the 60 development inputs',cost='all variants use the same 1000 objective-call budget')),
                rec('evaluation','evaluation-b.json',dict(**meta,dataset_sha='b'*64,independent_inputs=True,paired_repetitions=8,
                    paired_gain=[1.4,2.0,1.8,2.2,1.9,1.7,2.1,1.6],boundary_free_gain=0.0,limitation='Only two development distributions; no claim of global novelty.'))]
        elif n==2:
            second=[source,rec('evaluation','evaluation-a.json',dict(**meta,dataset_sha='a'*64,
                common_solved_subset=dict(count=40,base_mean=80,v7_mean=80),
                large_connected_subset=dict(count=20,base_completed=0,v7_completed=18,base_failures='wall timeout',v7_failures='2 wall timeouts',v7_quality_on_completed=79),
                caveat='No valid all-case mean exists for either method; common-subset averages are not whole-dataset scores.',
                limits=dict(wall_s=2,memory_mb=256),small_case_overhead='v7 uses 8% more operations')),
                rec('evaluation','evaluation-b.json',dict(**(meta|dict(unique_cases=20)),dataset_sha='b'*64,independent_inputs=True,
                    base_completed=1,v7_completed=17,limits=dict(wall_s=2,memory_mb=256),quality_constraint='all completed outputs pass the same validator'))]
        elif n==3:
            second=[source,rec('evaluation','evaluation-a.json',dict(**meta,dataset_sha='a'*64,domain='resource scheduling',
                paired_gain=[1.5,1.8,1.7,1.4,1.9,1.8],without_boundary_repair_gain=0.1,disconnected_graph_gain=0)),
                rec('source','candidate-v4.py',dict(kind='source comparison',base='static_groups(); independent_search()',
                    v4='adaptive_groups(); independent_search(); joint_frontier_repair()',same_code_as_a=False),job='stream-b'),
                rec('evaluation','evaluation-b.json',dict(**meta,dataset_sha='b'*64,domain='network assignment',
                    paired_gain=[1.8,1.6,1.9,1.7,1.5,1.8],without_joint_repair_gain=0.0,
                    shared_instances_with_a=0,shared_generator_with_a=False,disconnected_graph_gain=0),job='stream-b')]
        elif n==4:
            second=[rec('analysis','message',dict(text='The idea above has not been shared or coded by this author. It predicts concentration on coupled boundaries; the separate measurement below came from the existing routine evaluation stream.',shared=False)),
                rec('evaluation','evaluation-a.json',dict(**meta,dataset_sha='a'*64,author='other routine run',method='boundary-only joint correction',
                    grouped_paired_gain=dict(high_boundary=[3.0,2.8,3.1,2.7,3.2,2.9],disconnected=[0,0,0,0,0,0]),
                    independent_cases=True,variant_without_joint_step_gain=0.1,unresolved='Prediction for larger dense boundaries has not been tested')),
                rec('source','routine-variant.py',dict(kind='source inspection',algorithm='base solver plus exact recombination only on boundary variables',
                    full_search=False,source_sha='c'*64,comparison='same evaluator, inputs, objective-call and wall limits'))]
        elif n==5:
            second=[rec('source','candidate-v7.py',dict(kind='normalized source diff',v7='lazy cached marginal gain + single swap',
                    registered_method='cached marginal gains',semantic_change='only pool_size 8 -> 16; display name changed',algorithm_sha_ignoring_tuning='d'*64)),
                rec('evaluation','evaluation-a.json',dict(**meta,dataset_sha='a'*64,base_pool_8=80,v7_pool_16=96,
                    registered_cached_pool_16=96,registered_prior_result=96,coverage_change=False,
                    cost_tradeoff_new=False)),
                rec('material','registry-detail.json',dict(prior_findings='This exact pool-size effect and resource range were already documented before this run. No new applicability result.',method='cached marginal gains'))]
        else:
            second=[source,rec('evaluation','evaluation-a.json',dict(status='completed',dataset_purpose='development',valid=True,
                base_mean=80,v7_mean=100,base_objective_calls=1000,v7_objective_calls=4000,
                raw_cases=60,unique_cases=15,evaluator_sha='e'*64,cleanup_confirmed=True,infrastructure_error=False)),
                rec('evaluation','evaluation-b.json',dict(**meta,dataset_sha='b'*64,paired_repetitions=8,
                    matched_budget_base=80,matched_budget_v7=80,without_boundary_repair=80,
                    independent_inputs=True,selection='independent development inputs, frozen parameters'))]
        result.append(dict(id=f'case-{n:02}',jobs=jobs,phases=[first,second],rubric=dict(
            expected_signal=n<=4,require_jobs=jobs,require_mechanism=n<=4,
            limitations={1:'small effect and limited distributions',2:'missing whole-dataset means, 2/20 failures and overhead',3:'conditional cross-domain transfer, not universal',4:'unshared hypothesis versus measured support; dense-boundary prediction untested',5:'known method and tuning already documented',6:'unequal budget, duplicated inputs, matched-budget null effect'}[n])))
    return result

def variant(case,order):
    x=copy.deepcopy(case)
    if order=='b':
        # Same facts, reversed arrival order and repeated low-information logs.
        x['phases'][1].reverse()
        for i in range(4):
            base=copy.deepcopy(x['phases'][0][-1]);base.update(position=100+i,kind='progress',object='status')
            base['content']=encode(dict(mode='synthetic-replay',text='Routine progress; no additional scientific evidence.'))
            base['version']=sha(base['content']);x['phases'][1].append(base)
    return x
