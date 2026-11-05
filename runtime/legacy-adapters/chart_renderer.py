"""Fixed Matplotlib renderer; consumes trusted exported data, never model code."""
import io
import os
import tempfile


def draw(data):
    # Keep font/cache files out of the research run and user's global configuration.
    with tempfile.TemporaryDirectory(prefix='anysolvers-chart-') as cache:
        os.environ['MPLCONFIGDIR'] = cache
        import matplotlib
        matplotlib.use('Agg')
        from matplotlib.figure import Figure
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib import font_manager
        available = {f.name for f in font_manager.fontManager.ttflist}
        font = next((f for f in ['PingFang SC', 'Arial Unicode MS', 'Noto Sans CJK SC', 'DejaVu Sans'] if f in available), 'DejaVu Sans')
        with matplotlib.rc_context({'font.family':font, 'axes.unicode_minus':False, 'text.usetex':False,
                                   'axes.spines.top':False, 'axes.spines.right':False, 'font.size':10,
                                   'axes.titleweight':'semibold', 'axes.titlepad':16}):
            datasets = sorted({row['dataset_id'] for row in data['evaluations']})
            fig = Figure(figsize=(10, max(4.3, 3.8*len(datasets))), dpi=150, layout='constrained')
            FigureCanvasAgg(fig)
            axes = fig.subplots(len(datasets), 1, squeeze=False)[:,0]
            palette = ['#356EAD','#C66C46','#438579','#9976AF','#B38B35','#6D8295']
            for ax, dataset in zip(axes, datasets):
                rows = [r for r in data['evaluations'] if r['dataset_id']==dataset]
                purpose = rows[0]['dataset_purpose']
                ax.set_title(f'{dataset} · {purpose}', loc='left')
                ax.grid(axis='y', alpha=.17)
                ax.set_axisbelow(True)
                if data['chart']=='comparison':
                    for i, row in enumerate(rows):
                        color=palette[i%len(palette)]
                        if row['score'] is not None:
                            ax.bar(i, row['score'], color=color, width=.58)
                            ax.annotate(f"{row['score']:.4g}", (i,row['score']), xytext=(0,7), textcoords='offset points', ha='center')
                        else:
                            ax.scatter(i,0,marker='x',color='#AD5050',s=50)
                            ax.annotate('Unavailable', (i,0), xytext=(0,8), textcoords='offset points', ha='center')
                    labels=[r['candidate_id']+'\n'+r['evaluation_id']+f"\nvalid {r['valid_cases']}/{r['total_cases']}" for r in rows]
                    ax.set_xticks(range(len(rows)), labels, fontsize=8, rotation=15 if len(rows)>4 else 0)
                    ax.margins(y=.2)
                else:
                    labels=list(dict.fromkeys(g['label'] for row in rows for g in row['groups']))
                    try: labels.sort(key=float)
                    except ValueError: labels.sort()
                    for i,row in enumerate(rows):
                        values={g['label']:g['value'] for g in row['groups']}
                        ys=[values.get(k) for k in labels]
                        ax.plot(range(len(labels)),[float('nan') if y is None else y for y in ys],marker='o',markersize=3,
                                color=palette[i%len(palette)],label=row['candidate_id']+' · '+row['evaluation_id'])
                        missing=[x for x,y in enumerate(ys) if y is None]
                        if missing: ax.scatter(missing,[.02]*len(missing),transform=ax.get_xaxis_transform(),marker='x',color=palette[i%len(palette)])
                    stride=max(1,(len(labels)+19)//20)
                    ax.set_xticks(range(0,len(labels),stride),labels[::stride])
                    ax.set_xlabel(data['group_by'])
                    ax.legend(fontsize=8,frameon=False)
                ax.set_ylabel(data['metric']+(' · '+data['direction'] if data['chart']=='comparison' else ''))
            fig.suptitle(data['title'] or 'Evaluation evidence', fontsize=15, fontweight='semibold')
            fig.supxlabel('Failed/incomplete results are unavailable; × marks missing values. Development and final sets are separate.',fontsize=8,color='#626970')
            stream=io.BytesIO()
            fig.savefig(stream,format='png',metadata={'Software':'AnySolvers evidence renderer'})
            return stream.getvalue()
