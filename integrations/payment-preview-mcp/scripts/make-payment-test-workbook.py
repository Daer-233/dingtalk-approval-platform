"""Create only the synthetic 3-person payment preview fixture; never submit it."""
from pathlib import Path
from openpyxl import Workbook,load_workbook
from openpyxl.styles import Font,PatternFill
ROOT=Path(__file__).resolve().parents[1]
NAME='11月付款预览验收样表_合成员工_勿提交.xlsx'
HEADERS=['公司主体','姓名','一级部门','二级部门','三级部门','日常餐补','宵夜餐补','出差餐补']
ROWS=[['深圳再造','验收员工甲','财务管理中心','','',100,20,0],
      ['深圳再造','验收员工乙','供应链中心','','',60,10,30],
      ['深圳再造','验收员工丙','供应链中心','','',40,10,0]]


def validate_fixture(path):
    workbook=load_workbook(path,data_only=True,read_only=True)
    try:
        rows=list(workbook.active.values)
        normalized=[[None if value=='' else value for value in row] for row in [HEADERS,*ROWS]]
        if [list(row) for row in rows]!=normalized:
            raise ValueError('This is not the approved synthetic fixture; do not upload other workbooks with the acceptance script.')
    finally:workbook.close()


def main():
    folder=ROOT/'data/payments/acceptance';folder.mkdir(parents=True,exist_ok=True)
    path=folder/NAME
    if path.exists():
        validate_fixture(path)
    else:
        workbook=Workbook();sheet=workbook.active;sheet.title='合成样表_勿提交'
        for row in [HEADERS,*ROWS]:sheet.append(row)
        for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='24578A')
        for col in 'ABCDEFGH':sheet.column_dimensions[col].width=20
        sheet.freeze_panes='A2';workbook.save(path);validate_fixture(path)
    print('Synthetic fixture ready: 3 virtual people, 2 departments, total 270.00; do not submit approvals.')


if __name__=='__main__':main()